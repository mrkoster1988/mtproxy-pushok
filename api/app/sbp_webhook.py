"""Недоверенное уведомление только приближает GET-сверку известного счёта."""
import json
import os
import re

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter()
_MAX_BODY = 16384
_PAYMENT_ID = re.compile(r'^[a-zA-Z0-9-]{1,80}$')
_EVENTS = {'payment.succeeded', 'payment.canceled'}


def wake_payment(dsn, payment_id):
    """Один durable wake-up; повторы не снимают backoff или ручную сверку.

    Не сохраняем тело уведомления и не применяем его статус/сумму/metadata.
    Сбой транзакции не подтверждается отправителю: он сможет повторить доставку.
    """
    with psycopg.connect(dsn, connect_timeout=3,
                         options='-c lock_timeout=3000 -c statement_timeout=5000') as conn:
        conn.execute(
            """UPDATE payment_attempts SET sbp_webhook_received_at=now(),
                 sbp_next_check_at=CASE WHEN sbp_check_failures=0
                   THEN LEAST(sbp_next_check_at,now()) ELSE sbp_next_check_at END
               WHERE provider='sbp' AND payment_channel='external_web'
                 AND provider_payment_id=%s AND status='pending'
                 AND NOT sbp_requires_review AND sbp_webhook_received_at IS NULL""",
            (payment_id,))


@router.post('/api/v1/payments/sbp/webhook', tags=['payments'])
async def receive_sbp_notification(request: Request):
    if os.getenv('PUSHOK_SBP_WEBHOOK_ENABLED', '0') != '1':
        raise HTTPException(404, 'not_found')
    if os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') != '1':
        raise HTTPException(503, 'sbp_reconciliation_disabled')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'json_required')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, 'notification_too_large')
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, 'invalid_notification') from None
    if not isinstance(payload, dict) or payload.get('type') != 'notification':
        raise HTTPException(400, 'invalid_notification')
    event = payload.get('event')
    if not isinstance(event, str):
        raise HTTPException(400, 'invalid_notification')
    # Другие подписки магазина не вызывают бесконечную повторную доставку.
    if event not in _EVENTS:
        return Response(status_code=200, headers={'Cache-Control': 'no-store'})
    obj = payload.get('object')
    payment_id = obj.get('id') if isinstance(obj, dict) else None
    if not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id):
        raise HTTPException(400, 'invalid_notification')
    try:
        await run_in_threadpool(wake_payment, os.environ['DATABASE_URL'], payment_id)
    except (psycopg.Error, KeyError):
        raise HTTPException(503, 'notification_not_persisted') from None
    # Один и тот же ответ для неизвестного, повторного и известного платежа.
    return Response(status_code=200, headers={'Cache-Control': 'no-store'})
