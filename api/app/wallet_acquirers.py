"""Hosted RUB checkout. Only authenticated provider reads can confirm a deposit."""
import base64
import json
import os
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import UnsupportedAlgorithm
from . import control
from .sbp_provider import SBPProviderError, _NoRedirect
from .sbp_verification import SBPVerificationError

MINIMUM = {'platega': 1000, 'antilopay': 2000}
MAXIMUM = 1_000_000
PLATEGA = 'https://api.payplatform.cc'
ANTILOPAY = 'https://lk.antilopay.com/api/v1'


@lru_cache(maxsize=1)
def signing_key(encoded):
    try:
        key = serialization.load_der_private_key(base64.b64decode(encoded, validate=True), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return None
    return key if isinstance(key, rsa.RSAPrivateKey) else None


FIELDS = {'platega': ('MERCHANT_ID', 'SECRET'), 'antilopay': ('PROJECT_ID', 'SECRET_ID', 'PRIVATE_KEY_BASE64', 'VAT')}


def connection_values(provider):
    name = f'wallet-{provider}-private.json'
    keys = (*FIELDS[provider], 'ENABLED', 'TEST_MODE')
    if (control.root() / name).exists():
        saved = control.read(name)
        if not isinstance(saved, dict) or not all(isinstance(saved.get(k, ''), str) for k in keys):
            return {}
        return {k: saved.get(k, '') for k in keys}
    return {key: os.getenv(f'{provider.upper()}_{key}', '').strip() for key in keys}


def settings(provider, values=None):
    values = connection_values(provider) if values is None else values
    def get(name):
        return values.get(name, '')
    if provider == 'platega':
        account = get('MERCHANT_ID')
        creds = {'X-MerchantId': account, 'X-Secret': get('SECRET')}
        configured = bool(account and creds['X-Secret'])
    else:
        project, merchant = get('PROJECT_ID'), get('SECRET_ID')
        account = f'{merchant}/{project}'
        creds = {'project': project, 'merchant': merchant, 'key': signing_key(get('PRIVATE_KEY_BASE64'))}
        configured = bool(project and merchant and creds['key'] and get('VAT') in ('', '10', '22'))
        if get('VAT') in ('10', '22'):
            creds['vat'] = int(get('VAT'))
    return dict(shop=account, configured=configured, enabled=configured and get('ENABLED') == '1',
                host=os.getenv('PUSHOK_PUBLIC_PAYMENT_HOST', '').strip().lower(),
                test_mode=get('TEST_MODE') != '0', credentials=creds)


class InvoiceRejected(SBPProviderError):
    """Provider explicitly refused creation, so a new request is safe."""


def request(url, headers, body=None):
    req = Request(url, data=body, headers={**headers, 'Content-Type': 'application/json',
                                           'Accept': 'application/json'})
    try:
        with build_opener(_NoRedirect).open(req, timeout=6) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise SBPProviderError('response_too_large')
        data = json.loads(raw, parse_float=Decimal)
    except HTTPError as exc:
        if url == PLATEGA + '/v2/payment-links' and body and exc.code in (400, 422):
            raise InvoiceRejected('platega_checkout_rejected') from exc
        raise SBPProviderError('wallet_provider_unavailable') from exc
    except (URLError, OSError, ValueError) as exc:
        raise SBPProviderError('wallet_provider_unavailable') from exc
    if not isinstance(data, dict):
        raise SBPProviderError('invalid_response')
    return data


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()


def ant_request(cfg, endpoint, payload):
    credentials = cfg['credentials']
    body = encode({'project_identificator': credentials['project'], **payload})
    signature = credentials['key'].sign(body, padding.PKCS1v15(), hashes.SHA256())
    return request(ANTILOPAY + '/payment/' + endpoint, {
        'X-Apay-Secret-Id': credentials['merchant'], 'X-Apay-Sign-Version': '1',
        'X-Apay-Sign': base64.b64encode(signature).decode()}, body)


def matches(condition):
    if not condition:
        raise SBPVerificationError('wallet_provider_mismatch')


def kopecks(value):
    try:
        amount = Decimal(str(value))
        matches(amount.is_finite() and 0 <= amount <= 1000000)
        minor = amount * 100
        matches(minor == minor.to_integral_value())
        return int(minor)
    except (ValueError, InvalidOperation):
        raise SBPVerificationError('wallet_provider_amount') from None


def uuid_text(value):
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise SBPVerificationError('wallet_provider_id') from None


def invoice_url(value):
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        valid = parts and parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
    except ValueError:
        valid = False
    matches(valid)
    return value


def create(row, cfg):
    tid = str(row['id'])
    rubles = Decimal(row['amount']) / 100
    url = row['return_url']
    if row['provider'] == 'platega':
        body = dict(usageMode=1, paymentMode=0, currency='RUB', minAmount=format(rubles, '.2f'),
                    maxAmount=format(rubles, '.2f'), orderId=tid, payload=tid,
                    expiresAt=(row['created_at'] + timedelta(hours=24)).isoformat(),
                    description='Пополнение баланса Pushok')
        if url:
            body.update({'return': url, 'failedUrl': url})
        data = request(PLATEGA + '/v2/payment-links', {**cfg['credentials'], 'Idempotency-Key': tid}, encode(body))
        return uuid_text(data.get('id')), invoice_url(data.get('url'))

    # The merchant order ID is unique. Recover an invoice after an ambiguous POST.
    data = ant_request(cfg, 'check', {'order_id': tid})
    if data.get('code') == 8:
        body = dict(order_id=tid, currency='RUB', amount=float(rubles), product_name='Баланс Pushok',
                    product_type='services', description='Пополнение баланса Pushok',
                    prefer_methods=['SBP'], params={'direct_nspk': True}, customer={'email': row['receipt_email']})
        if url:
            body.update(success_url=url, fail_url=url)
        if 'vat' in cfg['credentials']:
            body['vat'] = cfg['credentials']['vat']
        data = ant_request(cfg, 'create', body)
        if data.get('code') in (3, 4, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18):
            raise InvoiceRejected('antilopay_checkout_rejected')
        if data.get('code') == 5:
            data = ant_request(cfg, 'check', {'order_id': tid})
    if data.get('code') != 0:
        raise SBPProviderError('antilopay_checkout_unavailable')
    if 'order_id' in data:
        matches(data['order_id'] == tid and data.get('currency') == 'RUB'
                and kopecks(data.get('original_amount')) == row['amount'])
    pid = data.get('payment_id')
    matches(isinstance(pid, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', pid)))
    return pid, invoice_url(data.get('payment_url'))


def inspect_payment(row, cfg):
    tid, pid = str(row['id']), row['provider_payment_id']
    if row['provider'] == 'platega':
        headers = cfg['credentials']
        link = request(PLATEGA + '/v2/payment-links/' + uuid_text(pid), headers)
        matches(link.get('id') == pid and link.get('orderId') == tid and link.get('usageMode') == 1)
        tx = link.get('consumedTransactionId')
        if not tx:
            matches(type(link.get('active')) is bool and not link.get('consumedAt'))
            return ('pending' if link['active'] else 'failed'), 0
        tx = uuid_text(tx)
        data = request(PLATEGA + '/transaction/' + tx, headers)
        details = data.get('paymentDetails')
        matches(data.get('id') == tx and data.get('payload') == tid and isinstance(details, dict)
                and details.get('currency') == 'RUB' and kopecks(details.get('amount')) == row['amount'])
        status = data.get('status')
        if status == 'CHARGEBACKED':
            return 'paid', row['amount']
        if status in ('PARTIALLY_REFUNDED', 'DISPUTED'):
            return 'review', 0
        matches(status in ('PENDING', 'CANCELED', 'CONFIRMED'))
        return {'PENDING': 'pending', 'CANCELED': 'failed', 'CONFIRMED': 'paid'}[status], 0

    data = ant_request(cfg, 'check', {'order_id': tid})
    if data.get('code') != 0:
        raise SBPProviderError('antilopay_check_unavailable')
    matches(data.get('order_id') == tid and data.get('payment_id') == pid
            and data.get('currency') == 'RUB' and kopecks(data.get('original_amount')) == row['amount'])
    status = data.get('status')
    if status in ('CHARGEBACK', 'REVERSED'):
        return 'paid', row['amount']
    if status == 'SUCCESS':
        refunds = data.get('refunds') or []
        matches(isinstance(refunds, list))
        amounts = {}
        for refund in refunds:
            matches(isinstance(refund, dict) and refund.get('status') in ('COMPLETE','PENDING','WAIT_ANSWER','FAIL'))
            if refund['status'] == 'COMPLETE':
                rid = refund.get('refund_id')
                matches(isinstance(rid, str) and rid)
                value = kopecks(refund.get('amount'))
                matches(amounts.setdefault(rid, value) == value)
        matches(sum(amounts.values()) <= row['amount'])
        return 'paid', sum(amounts.values())
    matches(status in ('PENDING', 'CANCEL', 'FAIL', 'EXPIRED'))
    return ('pending' if status == 'PENDING' else 'failed'), 0
