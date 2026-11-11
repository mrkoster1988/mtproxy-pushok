"""СБП внешнего сайта: устойчивый ключ запроса, владелец и сверка через GET.

Первый commit сохраняет ключ ДО обращения к банку. Неизвестный результат
не создаёт новую попытку. После 23 часов повторный POST запрещён: срок
идемпотентности провайдера ограничен 24 часами. Никаких изменений MTProxy.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException

from . import sbp_provider as provider
from .sbp_reconcile import reconcile_sbp_attempt, SBPReconcileError
from .sbp_verification import validate_sbp_identity, SBPVerificationError


def settings() -> dict:
    host = os.getenv('PUSHOK_PUBLIC_PAYMENT_HOST', '').strip().lower()
    shop = os.getenv('YOOKASSA_SHOP_ID', '')
    configured = bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                      and '..' not in host and shop and ':' not in shop
                      and not any(c.isspace() for c in shop)
                      and os.getenv('YOOKASSA_SECRET_KEY', ''))
    return {'enabled': provider.sbp_enabled() and configured,
            'configured': configured, 'host': host, 'shop': shop,
            'test_mode': os.getenv('PUSHOK_SBP_TEST_MODE', '1') != '0'}


def _campaign(conn, campaign_id: UUID, user_id: UUID):
    row = conn.execute(
        """SELECT id, advertiser_user_id, moderation_status, payment_status,
                  placement_status, price_kopecks_snapshot, currency_snapshot
           FROM campaigns WHERE id=%s AND advertiser_user_id=%s FOR UPDATE""",
        (campaign_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'campaign_not_found')
    return row


def _attempt(conn, campaign_id):
    return conn.execute(
        """SELECT * FROM payment_attempts WHERE campaign_id=%s AND provider='sbp'
           ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE""", (campaign_id,)).fetchone()


def _payable(campaign):
    return (campaign['moderation_status'] == 'approved'
            and campaign['payment_status'] == 'awaiting_payment'
            and campaign['placement_status'] == 'not_scheduled'
            and campaign['currency_snapshot'].strip() == 'RUB')


def _same_config(attempt, cfg):
    return (attempt['sbp_shop_id'] == cfg['shop']
            and attempt['sbp_test_mode'] is cfg['test_mode'])


def _same_return_host(attempt, cfg):
    return attempt['sbp_return_url'] == ('https://' + cfg['host'] + '/?sbp_campaign=' + str(attempt['campaign_id']))


def _view(campaign, attempt, cfg):
    continuation = bool(attempt and _same_config(attempt, cfg) and _same_return_host(attempt,cfg) and cfg['enabled']
                        and _payable(campaign) and attempt['status'] == 'pending')
    return {'campaign_id': str(campaign['id']),
            'available': cfg['enabled'], 'test_mode': attempt['sbp_test_mode'] if attempt else cfg['test_mode'],
            'amount_kopecks': attempt['amount'] if attempt else campaign['price_kopecks_snapshot'],
            'currency': 'RUB', 'status': attempt['status'] if attempt else 'not_created',
            'attempt_id': str(attempt['id']) if attempt else None,
            'can_create': cfg['enabled'] and _payable(campaign),
            'can_verify': bool(attempt and attempt['provider_payment_id']
                               and attempt['status'] in ('pending', 'succeeded')),
            'confirmation_url': attempt['sbp_confirmation_url'] if continuation else None,
            'payment_status': campaign['payment_status']}


def read_checkout(dsn, campaign_id, user_id):
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        campaign = _campaign(conn, campaign_id, user_id)
        return _view(campaign, _attempt(conn, campaign_id), settings())


def create_checkout(dsn, campaign_id, user_id):
    cfg = settings()
    if not cfg['enabled']:
        raise HTTPException(409, 'sbp_checkout_disabled_until_acceptance')
    # Отдельная транзакция: этот ключ переживает тайм-аут сети или rollback ниже.
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        campaign = _campaign(conn, campaign_id, user_id)
        if not _payable(campaign):
            raise HTTPException(409, 'campaign_not_payable')
        amount = campaign['price_kopecks_snapshot']
        if type(amount) is not int or not 100 <= amount <= 100000000:
            raise HTTPException(409, 'sbp_invalid_amount')
        conn.execute("""UPDATE payment_attempts SET status='failed'
                        WHERE campaign_id=%s AND provider='telegram_stars'
                          AND status='pending' AND expires_at<=now()
                          AND precheckout_approved_at IS NULL""", (campaign_id,))
        if conn.execute("""SELECT 1 FROM payment_attempts WHERE campaign_id=%s
                           AND (status='succeeded' OR (provider<>'sbp' AND status IN ('created','pending')))
                           LIMIT 1""", (campaign_id,)).fetchone():
            raise HTTPException(409, 'another_payment_in_progress')
        attempt = _attempt(conn, campaign_id)
        if not attempt or attempt['status'] == 'failed':
            conn.execute(
                """INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,
                       currency,status,sbp_return_url,sbp_shop_id,sbp_test_mode)
                   VALUES(%s,'sbp','external_web',%s,'RUB','created',%s,%s,%s)""",
                (campaign_id, amount, 'https://' + cfg['host'] + '/?sbp_campaign=' + str(campaign_id),
                 cfg['shop'], cfg['test_mode']))
    # Оба вызова checkout берут блокировку кампании в одном порядке.
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        campaign = _campaign(conn, campaign_id, user_id)
        attempt = _attempt(conn, campaign_id)
        if not attempt or not _same_config(attempt, cfg) or not _same_return_host(attempt,cfg):
            raise HTTPException(409, 'sbp_configuration_changed')
        if attempt['status'] in ('pending', 'succeeded'):
            return _view(campaign, attempt, cfg)
        if attempt['status'] != 'created' or not _payable(campaign):
            raise HTTPException(409, 'campaign_not_payable')
        if datetime.now(timezone.utc) - attempt['created_at'] >= timedelta(hours=23):
            raise HTTPException(409, 'sbp_creation_requires_review')
        result = provider.create_sbp_payment(
            campaign_id=str(campaign_id), amount_kopecks=attempt['amount'],
            idempotence_key=str(attempt['request_key']), return_url=attempt['sbp_return_url'],
            expected_test=attempt['sbp_test_mode'])
        updated = conn.execute(
            """UPDATE payment_attempts SET status='pending',provider_payment_id=%s,
                      sbp_confirmation_url=%s WHERE id=%s AND status='created' RETURNING *""",
            (result['payment_id'], result['confirmation_url'], attempt['id'])).fetchone()
        if updated is None:
            raise HTTPException(409, 'sbp_attempt_changed')
        conn.execute("""INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json)
                        VALUES(%s,%s,'sbp_invoice_created',jsonb_build_object('attempt_id',%s::text))""",
                     (user_id, campaign_id, str(attempt['id'])))
        return _view(campaign, updated, cfg)


def verify_checkout(dsn, campaign_id, user_id):
    # Сверка уже выставленного счёта работает и после выключения новых счетов.
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        campaign = _campaign(conn, campaign_id, user_id)
        attempt = _attempt(conn, campaign_id)
        if not attempt or not attempt['provider_payment_id']:
            raise HTTPException(409, 'sbp_invoice_not_created')
        cfg = settings()
        if not cfg['configured'] or not _same_config(attempt, cfg):
            raise HTTPException(409, 'sbp_configuration_changed')
        payment = provider.get_sbp_payment(attempt['provider_payment_id'])
        validate_sbp_identity(payment, expected_payment_id=attempt['provider_payment_id'],
                              expected_campaign_id=str(campaign_id), expected_kopecks=attempt['amount'],
                              expected_test=attempt['sbp_test_mode'])
        if payment.get('status') == 'succeeded':
            reconcile_sbp_attempt(conn, attempt['id'], fetch_payment=lambda _: payment)
        elif (payment.get('status') == 'canceled' and payment.get('paid') is False
              and attempt['status'] in ('pending', 'failed')):
            if attempt['status'] == 'pending':
                conn.execute("""UPDATE payment_attempts SET status='failed',sbp_confirmation_url=NULL
                                WHERE id=%s AND status='pending'""", (attempt['id'],))
                conn.execute("""INSERT INTO audit_events(actor_user_id,campaign_id,event_type)
                                VALUES(%s,%s,'sbp_payment_canceled')""", (user_id,campaign_id))
        elif payment.get('status') != 'pending' or attempt['status'] != 'pending':
            raise SBPReconcileError('payment_state_conflict')
        return _view(_campaign(conn, campaign_id, user_id), _attempt(conn, campaign_id), cfg)
