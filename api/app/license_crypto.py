"""Подписи лицензий: ключ продавца никогда не передаётся в установку покупателя."""
import base64
import fcntl
import hashlib
import json
import os
import time
from uuid import uuid4
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from .control import root


def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def raw(value):
    if not isinstance(value,str) or len(value)>100:raise ValueError('invalid_key')
    return base64.b64decode(value+'='*((-len(value))%4),altchars=b'-_',validate=True)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def sign(key,payload):return {'payload':payload,'signature':b64(key.sign(canonical(payload)))}
def verify(public,envelope):
    try:
        if set(envelope)!={'payload','signature'}:raise ValueError()
        Ed25519PublicKey.from_public_bytes(raw(public)).verify(raw(envelope['signature']),canonical(envelope['payload']))
        return envelope['payload']
    except (InvalidSignature,ValueError,TypeError,KeyError) as exc:raise ValueError('invalid_license_signature') from None

def key(name):
    if name not in ('seller','installation'):raise ValueError('invalid_key_name')
    directory=root();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/('license-'+name+'-key.json')
    with open(directory/'license-keys.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists():return Ed25519PrivateKey.from_private_bytes(raw(json.loads(path.read_text())['private_key']))
        private=Ed25519PrivateKey.generate()
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'private_key':b64(private.private_bytes_raw())},f);f.flush();os.fsync(f.fileno())
        return private

def identity(private=None):
    private=private or key('installation');public=b64(private.public_key().public_bytes_raw())
    return hashlib.sha256(raw(public)).hexdigest(),public

def request(operation,**fields):
    private=key('installation');iid,public=identity(private)
    return sign(private,{'installation_id':iid,'public_key':public,'operation':operation,'nonce':uuid4().hex,'issued_at':int(time.time()),**fields})

def check_request(envelope):
    p=envelope['payload'];verify(p['public_key'],envelope)
    if hashlib.sha256(raw(p['public_key'])).hexdigest()!=p['installation_id']:raise ValueError('installation_mismatch')
    if type(p['issued_at']) is not int or abs(time.time()-p['issued_at'])>120:raise ValueError('request_expired')
    if not isinstance(p['nonce'],str) or len(p['nonce'])!=32:raise ValueError('invalid_nonce')
    return p
