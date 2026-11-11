"""Счёт Stars для Mini App. Зачёт выполняет только existing bot successful_payment."""
import json
import os
import re
from datetime import datetime, timezone
from urllib.request import HTTPRedirectHandler, Request, build_opener

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from .bot_stars import prepare_for_user, StarsPrepareInput, parse_attempt
from .telegram_environment import bot_api_url, is_test_environment
from .payment_gate import stars_invoices_enabled


class StarsLinkError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def credentials():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not re.fullmatch(r'[1-9][0-9]{0,15}:[A-Za-z0-9_-]{10,256}', token):
        raise HTTPException(503, 'telegram_bot_token_not_configured')
    return token, int(token.split(':')[0])


def create_invoice_link(token, prepared):
    body = {'title': prepared.title, 'description': prepared.description,
            'payload': prepared.invoice_payload, 'provider_token': '', 'currency': 'XTR',
            'prices': [{'label': 'Спонсорство MTProxy', 'amount': prepared.stars_amount}]}
    request = Request(bot_api_url(token, 'createInvoiceLink'),
                      data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(_NoRedirect).open(request, timeout=8) as response:
            if response.status != 200:
                raise ValueError()
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        data = json.loads(raw)
        url = data.get('result')
        if data.get('ok') is not True or not isinstance(url, str) or not re.fullmatch(r'https://t\.me/\$[A-Za-z0-9_-]{1,256}', url):
            raise ValueError()
        return url
    except (OSError, ValueError, TypeError, AttributeError):
        # Исключения urllib могут содержать URL с токеном: наружу только фиксированный код.
        raise StarsLinkError('stars_link_unavailable') from None


def _owner(conn, cid, uid, *, lock=False):
    row = conn.execute("""SELECT c.id,c.payment_status,c.moderation_status,c.placement_status,
                          u.telegram_user_id,t.stars_price_xtr,t.is_active
                          FROM campaigns c JOIN users u ON u.id=c.advertiser_user_id
                          JOIN tariffs t ON t.id=c.tariff_id
                          WHERE c.id=%s AND c.advertiser_user_id=%s""" + (' FOR UPDATE OF c' if lock else ''), (cid, uid)).fetchone()
    if row is None:
        raise HTTPException(404, 'campaign_not_found')
    return row


def read_checkout(dsn, cid, uid):
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        campaign = _owner(conn, cid, uid)
        attempt = conn.execute("""SELECT id,status,amount,expires_at,precheckout_approved_at FROM payment_attempts
                                  WHERE campaign_id=%s AND provider='telegram_stars'
                                  ORDER BY created_at DESC,id DESC LIMIT 1""", (cid,)).fetchone()
    return {'payment_status': campaign['payment_status'], 'attempt_status': attempt['status'] if attempt else 'not_created',
            'amount_xtr': attempt['amount'] if attempt else campaign['stars_price_xtr'],
            'available': stars_invoices_enabled() and os.getenv('PUSHOK_MINIAPP_ENABLED', '0') == '1',
            'test_environment': is_test_environment(),
            'processing': bool(attempt and attempt['status'] == 'pending' and attempt['precheckout_approved_at'])}


def checkout(dsn, cid, uid):
    if os.getenv('PUSHOK_MINIAPP_ENABLED', '0') != '1' or not stars_invoices_enabled():
        raise HTTPException(409, 'stars_checkout_disabled_until_acceptance')
    token, bot_id = credentials()
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        owner = _owner(conn, cid, uid)
    prepared = prepare_for_user(dsn, StarsPrepareInput(campaign_id=cid, telegram_user_id=owner['telegram_user_id']))
    aid = parse_attempt(prepared.invoice_payload)
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row,
                         options='-c lock_timeout=5000 -c statement_timeout=15000') as conn:
        campaign = _owner(conn, cid, uid, lock=True)
        attempt = conn.execute('SELECT * FROM payment_attempts WHERE id=%s FOR UPDATE', (aid,)).fetchone()
        if (not attempt or attempt['status'] != 'pending' or attempt['expires_at'] is None or attempt['expires_at'] <= datetime.now(timezone.utc)
            or campaign['moderation_status'] != 'approved' or campaign['payment_status'] != 'awaiting_payment'
            or campaign['placement_status'] != 'not_scheduled' or not campaign['is_active']):
            raise HTTPException(409, 'campaign_not_payable')
        if attempt['telegram_test_environment'] != is_test_environment():
            raise HTTPException(409, 'stars_environment_changed')
        if attempt['precheckout_approved_at'] is not None:
            raise HTTPException(409, 'stars_payment_processing')
        if attempt['stars_invoice_bot_id'] not in (None, bot_id):
            raise HTTPException(409, 'stars_bot_changed')
        url = attempt['stars_invoice_url']
        if url and attempt['stars_invoice_bot_id'] != bot_id:
            raise HTTPException(409, 'stars_bot_changed')
        if url is None:
            url = create_invoice_link(token, prepared)
            conn.execute('UPDATE payment_attempts SET stars_invoice_url=%s,stars_invoice_bot_id=%s WHERE id=%s', (url, bot_id, aid))
        return {'invoice_url': url, 'amount_xtr': attempt['amount'], 'attempt_id': str(aid), 'currency': 'XTR'}
