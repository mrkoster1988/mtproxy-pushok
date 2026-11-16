"""Возвраты: сохранённое намерение, исходный способ оплаты, идемпотентность и сверка."""
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from uuid import UUID, uuid5, NAMESPACE_URL
from urllib.error import HTTPError
from urllib.request import Request, build_opener
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import wallet_ledger as ledger, sbp_provider as sbp
from .sbp_connection import current, serialized_creation
from .sbp_verification import validate_sbp_identity, SBPVerificationError
from .stars_checkout import credentials, _NoRedirect
from .telegram_environment import bot_api_url, is_test_environment


class RefundInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID
    amount: int | None=Field(None,gt=0,le=100000000,strict=True)
    reason: str=Field(min_length=3,max_length=500)
    manual_confirmed: bool=False

    @field_validator('reason')
    @classmethod
    def clean_reason(cls,value):
        value=value.strip()
        if len(value)<3:raise ValueError('reason_required')
        return value


class RefundReconcileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider_refund_id: str | None=Field(None,pattern=r'^[A-Za-z0-9_-]{1,128}$')


def admin_check(conn,admin):
    row=conn.execute('SELECT role FROM users WHERE id=%s FOR SHARE',(admin,)).fetchone()
    if not row or row['role']!='admin':raise HTTPException(403,'admin_role_required')


def locked_campaign(conn,cid):
    c=conn.execute('SELECT * FROM campaigns WHERE id=%s FOR UPDATE',(cid,)).fetchone()
    if not c:raise HTTPException(404,'campaign_not_found')
    return c


def payment(conn,c):
    p=conn.execute('SELECT * FROM wallet_payments WHERE campaign_id=%s FOR UPDATE',(c['id'],)).fetchone()
    if p:return 'wallet',p['currency'],p['cash_amount']+p['bonus_amount'],p
    rows=conn.execute("SELECT * FROM payment_attempts WHERE campaign_id=%s AND status IN ('succeeded','refunded') FOR UPDATE",(c['id'],)).fetchall()
    if len(rows)>1:raise HTTPException(409,'refund_payment_ambiguous')
    if rows:
        p=rows[0];return p['provider'],p['currency'].strip(),p['amount'],p
    return 'manual','RUB',c['price_kopecks_snapshot'],None


def prepare(conn,cid,data,admin,*,wallet_only=False):
    c=locked_campaign(conn,cid);admin_check(conn,admin)
    old=conn.execute('SELECT * FROM campaign_refunds WHERE request_id=%s',(data.request_id,)).fetchone()
    if old:
        if old['campaign_id']!=cid or old['actor_id']!=admin or old['reason']!=data.reason or (data.amount is not None and old['amount']!=data.amount) or old['manual_confirmed']!=data.manual_confirmed:
            raise HTTPException(409,'refund_request_conflict')
        return old
    source,currency,total,p=payment(conn,c)
    if wallet_only and source!='wallet':raise HTTPException(409,'wallet_payment_not_found')
    if c['payment_status']!='verified' or total<=0:raise HTTPException(409,'refund_not_paid')
    if conn.execute("SELECT 1 FROM campaign_refunds WHERE campaign_id=%s AND status IN ('created','pending','review')",(cid,)).fetchone():
        raise HTTPException(409,'refund_in_progress')
    remaining=total-c['refunded_amount'];amount=remaining if data.amount is None else data.amount
    if amount<=0 or amount>remaining:raise HTTPException(409,'refund_amount_exceeds_remaining')
    if source=='telegram_stars' and amount!=remaining:raise HTTPException(409,'refund_stars_full_only')
    if source=='sbp' and (amount<100 or 0<remaining-amount<100):raise HTTPException(409,'refund_sbp_minimum')
    if source=='manual' and not data.manual_confirmed:raise HTTPException(409,'refund_manual_confirmation')
    if c['placement_status']=='active' or (c['placement_status']=='scheduled' and
        (not c['scheduled_start_at'] or c['scheduled_start_at']<=datetime.now(timezone.utc) or
         conn.execute('SELECT 1 FROM placement_observations WHERE campaign_id=%s LIMIT 1',(cid,)).fetchone())):
        raise HTTPException(409,'wallet_stop_placement_first')
    conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s',(cid,))
    conn.execute("UPDATE campaigns SET auto_schedule_paused=true,placement_status=CASE WHEN placement_status IN ('scheduled','not_scheduled') THEN 'cancelled' ELSE placement_status END,updated_at=now() WHERE id=%s",(cid,))
    row=conn.execute('''INSERT INTO campaign_refunds(request_id,campaign_id,actor_id,source,currency,amount,reason,manual_confirmed)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',(data.request_id,cid,admin,source,currency,amount,data.reason,data.manual_confirmed)).fetchone()
    conn.execute("INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json) VALUES(%s,%s,'refund_requested',jsonb_build_object('refund_id',%s::uuid,'amount',%s::bigint,'source',%s::text))",(admin,cid,row['id'],amount,source))
    return row


def stars_refund(p):
    if p['telegram_test_environment']!=is_test_environment():raise HTTPException(409,'refund_provider_changed')
    token,bot_id=credentials()
    if p.get('stars_invoice_bot_id') and p['stars_invoice_bot_id']!=bot_id:raise HTTPException(409,'refund_provider_changed')
    if not p['payer_telegram_user_id']:raise HTTPException(409,'refund_payment_ambiguous')
    body={'user_id':p['payer_telegram_user_id'],'telegram_payment_charge_id':p['provider_payment_id']}
    req=Request(bot_api_url(token,'refundStarPayment'),data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        try:
            with build_opener(_NoRedirect).open(req,timeout=8) as response:raw=response.read(16385)
        except HTTPError as exc:
            raw=exc.read(16385)
        if len(raw)>16384:raise ValueError()
        result=json.loads(raw)
        if result.get('ok') is True and result.get('result') is True:return 'succeeded'
        if result.get('error_code')==400 and result.get('description')=='Bad Request: PAYMENT_ALREADY_REFUNDED':return 'succeeded'
    except (OSError,ValueError,TypeError,AttributeError):pass
    raise sbp.SBPProviderError('refund_provider_unavailable')


def cents(value):
    try:
        number=Decimal(value)
        if not number.is_finite() or number.as_tuple().exponent!=-2 or number<0:raise ValueError()
        return int(number*100)
    except (InvalidOperation,TypeError,ValueError):raise sbp.SBPProviderError('refund_invalid_response') from None


def sbp_refund(p,r,c):
    cfg=current()
    if cfg.get('shop')!=p['sbp_shop_id'] or cfg.get('test_mode')!=p['sbp_test_mode']:raise HTTPException(409,'refund_provider_changed')
    paid=sbp.get_sbp_payment(p['provider_payment_id'])
    validate_sbp_identity(paid,expected_payment_id=p['provider_payment_id'],expected_campaign_id=str(c['id']),expected_kopecks=p['amount'],expected_test=p['sbp_test_mode'])
    if paid.get('status')!='succeeded' or paid.get('paid') is not True:raise sbp.SBPProviderError('refund_invalid_payment')
    if r['provider_refund_id']:
        result=sbp._request('GET','https://api.yookassa.ru/v3/refunds/'+r['provider_refund_id'])
    else:
        # После срока действия idempotence-key повторный POST может создать второй возврат.
        if r['first_attempt_at'] and datetime.now(timezone.utc)-r['first_attempt_at']>timedelta(hours=23):raise HTTPException(409,'refund_manual_review_required')
        refunded=paid.get('refunded_amount',{'value':'0.00','currency':'RUB'})
        # Не принимаем неизвестный возврат за свой и не отправляем ещё один.
        # Если ответ POST потерян, администратор укажет ID из ЮKassa; затем только GET.
        if not isinstance(refunded,Mapping) or refunded.get('currency')!='RUB' or cents(refunded.get('value'))!=c['refunded_amount']:raise HTTPException(409,'refund_manual_review_required')
        result=sbp._request('POST','https://api.yookassa.ru/v3/refunds',idempotence_key=str(r['id']),body={
            'payment_id':p['provider_payment_id'],'amount':{'value':f"{r['amount']//100}.{r['amount']%100:02d}",'currency':'RUB'},
            'description':'Возврат puShok #'+str(c['id'])[:8]})
    if not isinstance(result,Mapping) or not isinstance(result.get('amount'),Mapping):raise sbp.SBPProviderError('refund_invalid_response')
    if (not isinstance(result.get('id'),str) or not sbp._PAYMENT_ID.fullmatch(result['id']) or
        (r['provider_refund_id'] and result['id']!=r['provider_refund_id']) or result.get('payment_id')!=p['provider_payment_id'] or
        result.get('amount',{}).get('currency')!='RUB' or cents(result.get('amount',{}).get('value'))!=r['amount'] or
        result.get('status') not in ('succeeded','pending','canceled')):raise sbp.SBPProviderError('refund_invalid_response')
    return result['status'],result['id']


def apply_success(conn,r,c,p,total):
    if r['source']=='wallet':
        # Сначала возвращаются бонусы, затем деньги; бонус не превращается в рубли.
        bonus=min(r['amount'],p['bonus_amount']-p['refunded_bonus']);cash=r['amount']-bonus
        for currency,amount in [('BONUS',bonus),(p['currency'],cash)]:
            if amount:ledger.post(conn,c['advertiser_user_id'],currency,amount,'refund','refund:'+str(r['id'])+':'+currency,r['reason'],actor=r['actor_id'],campaign=c['id'])
        conn.execute("UPDATE wallet_payments SET refunded_cash=refunded_cash+%s,refunded_bonus=refunded_bonus+%s,status=CASE WHEN refunded_cash+refunded_bonus+%s=cash_amount+bonus_amount THEN 'refunded' ELSE 'paid' END,refunded_at=now() WHERE id=%s",(cash,bonus,r['amount'],p['id']))
    refunded=c['refunded_amount']+r['amount']
    if refunded>total:raise HTTPException(409,'refund_amount_exceeds_remaining')
    conn.execute("UPDATE campaigns SET refunded_amount=%s,payment_status=CASE WHEN %s=%s THEN 'refunded' ELSE payment_status END,updated_at=now() WHERE id=%s",(refunded,refunded,total,c['id']))
    if p and r['source'] in ('sbp','telegram_stars') and refunded==total:
        conn.execute("UPDATE payment_attempts SET status='refunded' WHERE id=%s",(p['id'],))
    conn.execute("INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json) VALUES(%s,%s,'refund_succeeded',jsonb_build_object('refund_id',%s::uuid,'amount',%s::bigint))",(r['actor_id'],c['id'],r['id'],r['amount']))


@serialized_creation
def process(dsn,rid):
    # Отмечаем начало ДО сети: переживает остановку процесса и сбой после ответа провайдера.
    with psycopg.connect(dsn,connect_timeout=3) as conn:
        conn.execute("UPDATE campaign_refunds SET first_attempt_at=coalesce(first_attempt_at,now()) WHERE id=%s AND status NOT IN ('succeeded','canceled')",(rid,))
    with psycopg.connect(dsn,row_factory=dict_row,connect_timeout=3) as conn:
        row=conn.execute('SELECT campaign_id FROM campaign_refunds WHERE id=%s',(rid,)).fetchone()
        if not row:raise HTTPException(404,'refund_not_found')
        c=locked_campaign(conn,row['campaign_id']);r=conn.execute('SELECT * FROM campaign_refunds WHERE id=%s FOR UPDATE',(rid,)).fetchone()
        if r['status'] in ('succeeded','canceled'):return r
        source,currency,total,p=payment(conn,c)
        if source!=r['source'] or currency!=r['currency']:raise HTTPException(409,'refund_payment_ambiguous')
        status='succeeded';provider_id=r['provider_refund_id'];error=None
        try:
            if source=='sbp':status,provider_id=sbp_refund(p,r,c)
            elif source=='telegram_stars':status=stars_refund(p)
        except (sbp.SBPProviderError,SBPVerificationError,HTTPException) as exc:
            status='review';error=exc.detail if isinstance(exc,HTTPException) else 'refund_provider_unavailable'
        if status=='succeeded':apply_success(conn,r,c,p,total)
        return conn.execute("""UPDATE campaign_refunds SET status=%s,provider_refund_id=%s,error_code=%s,checked_at=now(),
            completed_at=CASE WHEN %s='succeeded' THEN now() ELSE NULL END WHERE id=%s RETURNING *""",(status,provider_id,error,status,rid)).fetchone()


def legacy_wallet_refund(conn,cid,admin,reason):
    c=locked_campaign(conn,cid);source,_,_,p=payment(conn,c)
    if source!='wallet':raise HTTPException(409,'wallet_payment_not_found')
    if p['status']=='refunded':return p
    r=prepare(conn,cid,RefundInput(request_id=uuid5(NAMESPACE_URL,'pushok-wallet-refund:'+str(cid)),reason=reason),admin,wallet_only=True)
    if r['status']!='succeeded':
        c=locked_campaign(conn,cid);_,_,total,p=payment(conn,c);apply_success(conn,r,c,p,total)
        conn.execute("UPDATE campaign_refunds SET status='succeeded',completed_at=now() WHERE id=%s",(r['id'],))
    return conn.execute('SELECT * FROM wallet_payments WHERE id=%s',(p['id'],)).fetchone()


def service_once(dsn,limit=20):
    with psycopg.connect(dsn,row_factory=dict_row,connect_timeout=3) as conn:
        rows=conn.execute("SELECT id FROM campaign_refunds WHERE status IN ('created','pending') AND (checked_at IS NULL OR checked_at<now()-interval '1 minute') ORDER BY created_at LIMIT %s",(limit,)).fetchall()
    for row in rows:process(dsn,row['id'])
    return {'checked':len(rows)}


def router_for(get_dsn,require_admin):
    router=APIRouter(prefix='/api/v1/admin/campaigns')
    @router.get('/{cid}/refunds')
    def read(cid:UUID,response:Response,admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:
            admin_check(conn,admin['id']);c=locked_campaign(conn,cid);source,currency,total,p=payment(conn,c)
            rows=conn.execute('SELECT * FROM campaign_refunds WHERE campaign_id=%s ORDER BY created_at DESC',(cid,)).fetchall()
            return {'source':source,'currency':currency,'paid':total,'refunded':c['refunded_amount'],'remaining':max(0,total-c['refunded_amount']),
                    'paid_confirmed':c['payment_status'] in ('verified','refunded'),'partial_allowed':source!='telegram_stars','items':rows}
    @router.post('/{cid}/refunds')
    def create(cid:UUID,data:RefundInput,response:Response,admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:r=prepare(conn,cid,data,admin['id'])
        return process(get_dsn(),r['id'])
    @router.post('/{cid}/refunds/{rid}/retry')
    def retry(cid:UUID,rid:UUID,response:Response,data:RefundReconcileInput | None=None,admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:
            admin_check(conn,admin['id'])
            locked_campaign(conn,cid)
            row=conn.execute('SELECT * FROM campaign_refunds WHERE id=%s AND campaign_id=%s FOR UPDATE',(rid,cid)).fetchone()
            if not row:raise HTTPException(404,'refund_not_found')
            if data and data.provider_refund_id:
                if row['source']!='sbp' or row['status']!='review':raise HTTPException(409,'refund_request_conflict')
                # Неверный ID не сохраняется до серверной проверки связи, суммы и статуса.
                c=locked_campaign(conn,cid);_,_,_,p=payment(conn,c)
                candidate={**row,'provider_refund_id':data.provider_refund_id}
                try:sbp_refund(p,candidate,c)
                except (sbp.SBPProviderError,SBPVerificationError,HTTPException) as exc:raise HTTPException(409,'refund_manual_review_required') from exc
                conn.execute('UPDATE campaign_refunds SET provider_refund_id=%s WHERE id=%s',(data.provider_refund_id,rid))
        return process(get_dsn(),rid)
    return router
