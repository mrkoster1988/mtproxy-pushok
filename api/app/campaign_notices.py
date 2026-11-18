"""Приватная очередь запуска/завершения/сбоев; lease ACK только после отправки."""
import os
from uuid import UUID
from typing import Literal
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel
from .bot_routes import _verify_bot_secret

FAILURE_CODES=frozenset({'apply_failed','restore_failed','ack_unavailable','runtime_unavailable',
                        'binding_missing','binding_changed','default_binding_missing','conflicting_campaigns'})

def record_failure(conn,node_id,code):
    if code not in FAILURE_CODES:
        return
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('incident:'+str(node_id),))
    old=conn.execute('SELECT code,resolved_at FROM promotion_incidents WHERE proxy_node_id=%s FOR UPDATE',(node_id,)).fetchone()
    conn.execute('''INSERT INTO promotion_incidents(proxy_node_id,code) VALUES(%s,%s)
        ON CONFLICT(proxy_node_id) DO UPDATE SET code=EXCLUDED.code,checked_at=now(),resolved_at=NULL''',(node_id,code))
    if old and old['resolved_at'] is None and old['code'] == code:
        return
    if code == 'default_binding_missing':
        conn.execute("SELECT pushok_enqueue_lifecycle(NULL,'failed',%s)", (code,))
        return
    rows=conn.execute("""SELECT id FROM campaigns WHERE proxy_node_id=%s
        AND (placement_status='active' OR (placement_status='scheduled' AND scheduled_start_at<=now() AND scheduled_end_at>now()))
        ORDER BY scheduled_start_at,id LIMIT 2""",(node_id,)).fetchall()
    for campaign in rows or [{'id':None}]:
        conn.execute("SELECT pushok_enqueue_lifecycle(%s,'failed',%s)",(campaign['id'],code))

router=APIRouter(prefix='/api/v1/internal/bot/lifecycle/outbox',include_in_schema=False)
class Notice(BaseModel):
    notice_id: UUID
    claim_token: UUID
    campaign_id: UUID | None
    telegram_user_id: int
    audience: Literal['owner','staff']
    event_type: Literal['started','completed','failed']
    reason: str | None = None
    channel_title: str | None = None
class Finish(BaseModel):
    notice_id: UUID
    claim_token: UUID
    permanent_failure: bool = False

@router.post('/claim',response_model=list[Notice])
def claim(response: Response,x_pushok_bot_secret: str | None=Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret);response.headers['Cache-Control']='no-store'
    try:
        with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=3,row_factory=dict_row) as conn:
            return conn.execute("""WITH due AS (
              SELECT n.id FROM campaign_notice_outbox n JOIN users u ON u.id=n.recipient_user_id
              WHERE n.delivered_at IS NULL AND n.abandoned_at IS NULL AND n.next_attempt_at<=now()
              AND (n.lease_until IS NULL OR n.lease_until<now()) AND u.telegram_user_id>0
              AND ((n.audience='owner' AND EXISTS(SELECT 1 FROM campaigns c WHERE c.id=n.campaign_id AND c.advertiser_user_id=u.id))
                OR (n.audience='staff' AND u.role IN ('admin','operator') AND EXISTS(SELECT 1 FROM bot_operator_subscriptions s WHERE s.operator_user_id=u.id)))
              ORDER BY n.next_attempt_at,n.id LIMIT 5 FOR UPDATE OF n SKIP LOCKED
            ), claimed AS (
              UPDATE campaign_notice_outbox n SET attempts=n.attempts+1,claim_token=gen_random_uuid(),
                lease_until=now()+interval '90 seconds',
                next_attempt_at=now()+interval '2 minutes'*LEAST(n.attempts+1,30)
              FROM due WHERE n.id=due.id RETURNING n.*
            ) SELECT n.id AS notice_id,n.claim_token,n.campaign_id,u.telegram_user_id,n.audience,n.event_type,
                CASE WHEN n.audience='staff' THEN n.reason ELSE NULL END AS reason,ch.title AS channel_title
              FROM claimed n JOIN users u ON u.id=n.recipient_user_id
              LEFT JOIN campaigns c ON c.id=n.campaign_id LEFT JOIN sponsor_channels ch ON ch.id=c.channel_id""").fetchall()
    except psycopg.Error as exc:
        raise HTTPException(503,'database_unavailable') from exc

@router.post('/finish')
def finish(payload: Finish,response: Response,x_pushok_bot_secret: str | None=Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret);response.headers['Cache-Control']='no-store'
    try:
        with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=3) as conn:
            row=conn.execute('''UPDATE campaign_notice_outbox SET
                delivered_at=CASE WHEN NOT %s THEN now() ELSE delivered_at END,
                abandoned_at=CASE WHEN %s THEN now() ELSE abandoned_at END,claim_token=NULL,lease_until=NULL
                WHERE id=%s AND claim_token=%s AND lease_until>=now()
                AND delivered_at IS NULL AND abandoned_at IS NULL RETURNING id''',
                (payload.permanent_failure,payload.permanent_failure,payload.notice_id,payload.claim_token)).fetchone()
            if not row:
                raise HTTPException(409,'notice_claim_expired')
    except psycopg.Error as exc:
        raise HTTPException(503,'database_unavailable') from exc
    return {'acknowledged':True}
