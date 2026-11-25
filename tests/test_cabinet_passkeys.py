import base64
import hashlib
import json
import os
import secrets
import struct
import unittest
from unittest.mock import patch
from uuid import uuid4

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import psycopg
import test_postgres as fixture

main = fixture.main
SITE = 'https://pushok.shop'
HEADERS = {'Origin': SITE}
PATH = '/api/v1/auth/passkeys'


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class PasskeyOriginTests(unittest.TestCase):
    def test_login_options_require_the_configured_origin(self):
        client = TestClient(main.app, base_url=SITE)
        with patch.dict(os.environ, {'PUSHOK_MINIAPP_URL':SITE+'/miniapp'}), patch.object(main,'SESSION_SIGNING_KEY','x'*40):
            self.assertEqual(client.post(PATH+'/login/options',headers={'Origin':'https://other.example'}).status_code,403)
            res = client.post(PATH+'/login/options', headers=HEADERS)
            self.assertEqual(res.json()['rpId'], 'pushok.shop')
            self.assertEqual(res.json()['userVerification'], 'required')
            self.assertIn('HttpOnly', res.headers['set-cookie'])
            self.assertEqual(client.post(PATH+'/register/options', headers=HEADERS).status_code,401)


@unittest.skipUnless(fixture._approved_test_postgres(), 'isolated PostgreSQL required')
class CabinetPasskeyIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)

    def test_registered_passkey_logs_into_original_account_only_once(self):
        uid, sid = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as db:
            db.execute("INSERT INTO users(id, telegram_user_id, first_name) VALUES (%s,%s,'Test')",
                       (uid, int(uuid4().hex[:12],16)))
            db.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES (%s,%s,now()+interval '1 hour')", (sid,uid))
        first = TestClient(main.app, base_url=SITE)
        fresh = TestClient(main.app, base_url=SITE)
        with patch.dict(os.environ, {'PUSHOK_MINIAPP_URL':SITE}), patch.object(main,'SESSION_SIGNING_KEY','x'*40):
            auth = dict(HEADERS, Authorization='Bearer '+main._create_session_token(uid,sid))
            options = first.post(PATH+'/register/options', headers=auth).json()
            private = ec.generate_private_key(ec.SECP256R1())
            public = private.public_key().public_numbers()
            cose = cbor2.dumps({1:2,3:-7,-1:1,-2:public.x.to_bytes(32,'big'),-3:public.y.to_bytes(32,'big')})
            cred = secrets.token_bytes(32)
            rp = hashlib.sha256(b'pushok.shop').digest()
            def client_data(kind, nonce):
                return json.dumps({'type':kind,'challenge':nonce,'origin':SITE}).encode()
            cd = client_data('webauthn.create',options['challenge'])
            att = cbor2.dumps({'fmt':'none','attStmt':{},'authData':
                rp + bytes([0x45]) + bytes(4+16) + struct.pack('>H',len(cred)) + cred + cose})
            identity = {'id':b64(cred),'rawId':b64(cred),'type':'public-key'}
            registration = dict(identity,response={'clientDataJSON':b64(cd),'attestationObject':b64(att)})
            self.assertEqual(first.post(PATH+'/register/finish',headers=auth,
                json={'credential':json.dumps(registration)}).status_code,200)
            nonce = fresh.post(PATH+'/login/options',headers=HEADERS).json()['challenge']
            challenge_cookie = fresh.cookies.get('__Secure-pushok-key-challenge')
            cd = client_data('webauthn.get',nonce)
            ad = rp + bytes([5]) + struct.pack('>I',1)
            sig = private.sign(ad+hashlib.sha256(cd).digest(),ec.ECDSA(hashes.SHA256()))
            assertion = dict(identity,response={'clientDataJSON':b64(cd),'authenticatorData':b64(ad),
                             'signature':b64(sig),'userHandle':b64(uid.bytes)})
            proof = {'credential':json.dumps(assertion)}
            resp = fresh.post(PATH+'/login/finish',headers=HEADERS,json=proof)
            self.assertEqual(resp.status_code,200,resp.text)
            self.assertEqual(main._decode_session_token(resp.json()['access_token'])[0],uid)
            replay = TestClient(main.app,base_url=SITE)
            replay.cookies.set('__Secure-pushok-key-challenge',challenge_cookie,path=PATH)
            self.assertEqual(replay.post(PATH+'/login/finish',headers=HEADERS,json=proof).status_code,401)
