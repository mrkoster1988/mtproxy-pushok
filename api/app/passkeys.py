"""Discoverable WebAuthn credentials belonging to existing cabinet accounts."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg
from psycopg.rows import dict_row
from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response, verify_registration_response)
from webauthn.helpers import parse_authentication_credential_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

PATH = '/api/v1/auth/passkeys'
COOKIE = '__Secure-pushok-key-challenge'
TTL = 180


class Proof(BaseModel):
    credential: str = Field(min_length=2, max_length=32000)


def origin(request):
    config = urlsplit(os.environ.get('PUSHOK_MINIAPP_URL') or 'https://pushok.shop')
    if (config.scheme != 'https' or not config.hostname or config.username or config.password
            or request.headers.get('origin') != 'https://' + config.netloc):
        raise HTTPException(403, 'passkey_origin_required')
    return config.hostname, 'https://' + config.netloc


def options_response(options, action, owner, key):
    value = '.'.join((str(int(time.time()) + TTL), action, owner, options.challenge.hex()))
    signature = hmac.new(key, ('cabinet-passkey-v1:' + value).encode(), 'sha256').hexdigest()
    res = JSONResponse(json.loads(options_to_json(options)), headers={'Cache-Control':'private, no-store'})
    res.set_cookie(COOKIE, value + '.' + signature, max_age=TTL, secure=True,
                   httponly=True, samesite='strict', path=PATH)
    return res


def challenge(request, action, owner, key):
    parts = request.cookies.get(COOKIE, '').split('.')
    if len(parts) == 5 and parts[1:3] == [action, owner]:
        value, signature = '.'.join(parts[:4]), parts[4]
        expected = hmac.new(key, ('cabinet-passkey-v1:' + value).encode(), 'sha256').hexdigest()
        if hmac.compare_digest(expected, signature):
            expiry = int(parts[0])
            if time.time() < expiry <= time.time() + TTL:
                return bytes.fromhex(parts[3])
    raise HTTPException(401, 'passkey_challenge_expired')


def consume(db, value):
    db.execute('DELETE FROM cabinet_used_challenges WHERE expires_at < now()')
    row = db.execute("INSERT INTO cabinet_used_challenges(digest, expires_at) VALUES (%s, now() + interval '4 minutes') ON CONFLICT DO NOTHING RETURNING digest",
                     (hashlib.sha256(value).digest(),)).fetchone()
    if row is None:
        raise HTTPException(401, 'passkey_challenge_expired')


def router(database, current_user, signing_key, session_token, remember_session, session_ttl):
    api = APIRouter(prefix=PATH, tags=['authentication'])

    def connect():
        return psycopg.connect(database(), connect_timeout=3, row_factory=dict_row)

    @api.post('/register/options')
    def register_options(request: Request, user=Depends(current_user)):
        host, _ = origin(request)
        with connect() as db:
            rows = db.execute('SELECT credential_id FROM cabinet_passkeys WHERE user_id=%s', (user['id'],)).fetchall()
        if len(rows) >= 8:
            raise HTTPException(409, 'passkey_limit')
        options = generate_registration_options(rp_id=host, rp_name='Pushok MTProxy',
            user_id=UUID(str(user['id'])).bytes, user_name=str(user['telegram_user_id']),
            user_display_name=user.get('first_name') or 'Pushok', timeout=TTL*1000,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(r['credential_id'])) for r in rows],
            authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                require_resident_key=True, user_verification=UserVerificationRequirement.REQUIRED))
        return options_response(options, 'register', str(user['session_id']), signing_key())

    @api.post('/register/finish')
    def register(request: Request, proof: Proof, user=Depends(current_user)):
        host, site = origin(request)
        nonce = challenge(request, 'register', str(user['session_id']), signing_key())
        try:
            key = verify_registration_response(credential=proof.credential, expected_challenge=nonce,
                expected_rp_id=host, expected_origin=site, require_user_verification=True)
        except (WebAuthnException, ValueError, TypeError, KeyError):
            raise HTTPException(400, 'passkey_invalid') from None
        with connect() as db:
            db.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (user['id'],))
            count = db.execute('SELECT count(*) AS n FROM cabinet_passkeys WHERE user_id=%s', (user['id'],)).fetchone()
            if count['n'] >= 8:
                raise HTTPException(409, 'passkey_limit')
            consume(db, nonce)
            row = db.execute('INSERT INTO cabinet_passkeys(credential_id,user_id,public_key,sign_count) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING user_id',
                             (key.credential_id, user['id'], key.credential_public_key, key.sign_count)).fetchone()
            if row is None:
                raise HTTPException(409, 'passkey_already_registered')
        return {'registered':True}

    @api.post('/login/options')
    def login_options(request: Request):
        host, _ = origin(request)
        options = generate_authentication_options(rp_id=host, timeout=TTL*1000,
                                                  user_verification=UserVerificationRequirement.REQUIRED)
        return options_response(options, 'login', '-', signing_key())

    @api.post('/login/finish')
    def login(request: Request, proof: Proof):
        host, site = origin(request)
        nonce = challenge(request, 'login', '-', signing_key())
        with connect() as db:
            try:
                credential = parse_authentication_credential_json(proof.credential)
                row = db.execute('SELECT k.* FROM cabinet_passkeys k JOIN users u ON u.id=k.user_id WHERE credential_id=%s AND u.blocked_at IS NULL FOR UPDATE OF u,k',
                                 (credential.raw_id,)).fetchone()
                if row is None or credential.response.user_handle != row['user_id'].bytes:
                    raise HTTPException(401, 'passkey_invalid')
                verified = verify_authentication_response(credential=credential, expected_challenge=nonce,
                    expected_rp_id=host, expected_origin=site, credential_public_key=bytes(row['public_key']),
                    credential_current_sign_count=row['sign_count'], require_user_verification=True)
            except (WebAuthnException, ValueError, TypeError, KeyError):
                raise HTTPException(401, 'passkey_invalid') from None
            consume(db, nonce)
            db.execute('UPDATE cabinet_passkeys SET sign_count=%s WHERE credential_id=%s',
                       (verified.new_sign_count, credential.raw_id))
            sid = uuid4()
            db.execute("INSERT INTO user_sessions(id,user_id,expires_at,login_surface) VALUES (%s,%s,%s,'web')",
                       (sid, row['user_id'], datetime.now(timezone.utc) + timedelta(seconds=session_ttl)))
            token = session_token(row['user_id'], sid)
        res = JSONResponse({'access_token':token, 'token_type':'bearer', 'expires_in':session_ttl})
        remember_session(res, token, session_ttl)
        res.delete_cookie(COOKIE, path=PATH, secure=True, httponly=True, samesite='strict')
        return res

    @api.post('/remove')
    def remove(request: Request, user=Depends(current_user)):
        origin(request)
        with connect() as db:
            db.execute('DELETE FROM cabinet_passkeys WHERE user_id=%s', (user['id'],))
        return {'removed':True}

    return api
