"""Очередь администратора: только постановка GET-сверки, без ручного зачёта."""
import os

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from .sbp_checkout import settings, _same_config


def list_attempts(dsn, *, offset=0, limit=50):
    cfg = settings()
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        rows = conn.execute(
            """SELECT id,campaign_id,provider_payment_id,amount,currency,status,created_at,
                      sbp_shop_id,sbp_test_mode,sbp_requires_review,sbp_check_failures,
                      sbp_next_check_at,sbp_check_result,sbp_check_revision
               FROM payment_attempts WHERE provider='sbp' AND status IN ('created','pending')
               ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""", (limit + 1, offset)).fetchall()
    items = []
    enabled = os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') == '1'
    for row in rows[:limit]:
        matches = cfg['configured'] and _same_config(row, cfg)
        reason = ('creation_unknown' if not row['provider_payment_id'] else
                  'configuration_changed' if not matches else
                  'verification_requires_review' if row['sbp_requires_review'] else
                  'provider_retry' if row['sbp_check_failures'] else 'awaiting_provider')
        items.append({key: row[key] for key in (
            'id', 'campaign_id', 'provider_payment_id', 'amount', 'currency', 'status', 'created_at',
            'sbp_test_mode', 'sbp_requires_review', 'sbp_check_failures', 'sbp_next_check_at',
            'sbp_check_revision')})
        items[-1].update(reason=reason, can_retry=bool(enabled and matches and
            row['provider_payment_id'] and row['status'] == 'pending'))
    return {'items': items, 'has_more': len(rows) > limit, 'offset': offset,
            'background_enabled': enabled}


def request_recheck(dsn, attempt_id, admin_id, revision, note):
    if os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') != '1':
        raise HTTPException(409, 'sbp_reconciliation_disabled')
    note = note.strip()
    if not 12 <= len(note) <= 300:
        raise HTTPException(422, 'sbp_review_note_required')
    cfg = settings()
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row,
                         options='-c lock_timeout=5000 -c statement_timeout=10000') as conn:
        conn.execute('''SELECT c.id FROM campaigns c JOIN payment_attempts a ON a.campaign_id=c.id
                        WHERE a.id=%s AND a.provider='sbp' FOR UPDATE OF c''', (attempt_id,)).fetchone()
        row = conn.execute("SELECT * FROM payment_attempts WHERE id=%s AND provider='sbp' FOR UPDATE",
                           (attempt_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'sbp_attempt_not_found')
        if row['status'] != 'pending' or not row['provider_payment_id']:
            raise HTTPException(409, 'sbp_attempt_not_retryable')
        if row['sbp_check_revision'] != revision:
            raise HTTPException(409, 'sbp_attempt_changed')
        if not cfg['configured'] or not _same_config(row, cfg):
            raise HTTPException(409, 'sbp_configuration_changed')
        conn.execute("""UPDATE payment_attempts SET sbp_requires_review=false,sbp_check_failures=0,
                        sbp_next_check_at=now(),sbp_check_result='admin_retry_queued',
                        sbp_check_revision=sbp_check_revision+1 WHERE id=%s""", (attempt_id,))
        conn.execute("""INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json)
                        VALUES(%s,%s,'sbp_admin_retry_requested',jsonb_build_object(
                            'attempt_id',%s::text,'note',%s::text))""",
                     (admin_id, row['campaign_id'], str(attempt_id), note))
    return {'status': 'queued', 'attempt_id': str(attempt_id)}
