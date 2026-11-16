"""Покупаемые лицензии кабинета; условия закреплены в заказе, активация только после оплаты."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .bot_stars import StarsPrecheckoutInput, StarsReceiptInput, authorize
from .payment_gate import stars_invoices_enabled
from .telegram_environment import is_test_environment
from . import stars_checkout


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Plan(Input):
    name:str=Field(min_length=1,max_length=80)
    days:int=Field(ge=1,le=3650,strict=True)
    price_xtr:int=Field(ge=0,le=1000000,strict=True)
    max_channels:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    max_open_campaigns:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    max_campaigns_per_period:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    active:bool=Field(default=False,strict=True)
    @field_validator('name')
    @classmethod
    def nonempty(cls,v):
        if not v.strip():raise ValueError('Укажите название')
        return v.strip()
class Settings(Input):
    enabled:bool=Field(strict=True)
class Purchase(Input):
    plan_id:UUID
    revision:int=Field(ge=1,strict=True)
class Refund(Input):
    telegram_user_id:int=Field(gt=0,strict=True)
    currency:str=Field(pattern='^XTR$')
    total_amount:int=Field(gt=0,le=1000000,strict=True)
    telegram_payment_charge_id:str=Field(min_length=1,max_length=255)


def parse_order(payload):
    if not isinstance(payload,str) or not payload.startswith('license:'):return None
    try:uid=UUID(payload[8:])
    except (ValueError,TypeError):return None
    return uid if payload=='license:'+str(uid) else None


def event(conn,uid,kind,details):
    conn.execute('INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,%s,%s::jsonb)',(uid,kind,json.dumps(details,default=str)))


def activate(conn,order):
    """Вызывается под блокировкой пользователя; повтор не продлевает срок второй раз."""
    if order['test_mode']:return
    if conn.execute('SELECT 1 FROM license_grants WHERE order_id=%s',(order['id'],)).fetchone():return
    start=conn.execute('SELECT GREATEST(now(),COALESCE(max(ends_at),now())) AS start FROM license_grants WHERE user_id=%s AND revoked_at IS NULL',(order['user_id'],)).fetchone()['start']
    conn.execute('INSERT INTO license_grants(user_id,order_id,terms,starts_at,ends_at) VALUES(%s,%s,%s::jsonb,%s,%s)',
                 (order['user_id'],order['id'],json.dumps(order['terms']),start,start+timedelta(days=order['terms']['days'])))
    event(conn,order['user_id'],'license_activated',{'order_id':order['id'],'starts_at':start,'days':order['terms']['days']})


def locked_order(conn,oid):
    owner=conn.execute('SELECT user_id FROM license_orders WHERE id=%s',(oid,)).fetchone()
    if not owner:return None
    user=conn.execute('SELECT telegram_user_id FROM users WHERE id=%s FOR UPDATE',(owner['user_id'],)).fetchone()
    row=conn.execute('SELECT * FROM license_orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
    return dict(row,telegram_user_id=user['telegram_user_id'])


def contract(order,data):
    return bool(order and order['telegram_user_id']==data.telegram_user_id and data.currency=='XTR'
                and type(data.total_amount) is int and data.total_amount==order['amount']
                and order['test_mode']==is_test_environment()
                and order['bot_id']==stars_checkout.credentials()[1])


def apply_refund(conn,order):
    r=conn.execute('SELECT * FROM license_refunds WHERE charge_id=%s',(order['charge_id'],)).fetchone()
    if not r or r['amount']!=order['amount'] or r['telegram_user_id']!=order['telegram_user_id'] or r['bot_id']!=order['bot_id'] or r['test_mode']!=order['test_mode']:return False
    conn.execute("UPDATE license_orders SET status='refunded' WHERE id=%s",(order['id'],))
    conn.execute('UPDATE license_grants SET revoked_at=COALESCE(revoked_at,now()) WHERE order_id=%s',(order['id'],))
    conn.execute('UPDATE license_refunds SET applied=true WHERE charge_id=%s',(order['charge_id'],))
    if not r['applied']:event(conn,order['user_id'],'license_refunded',{'order_id':order['id']})
    return True


def router_for(get_dsn,current_user,require_admin):
    router=APIRouter()
    @contextmanager
    def db():
        try:
            with psycopg.connect(get_dsn(),connect_timeout=3,row_factory=dict_row,options='-c lock_timeout=5000 -c statement_timeout=15000') as conn:yield conn
        except psycopg.Error as exc:raise HTTPException(503,'database_unavailable') from exc

    @router.get('/api/v1/licenses')
    def own(response:Response,user=Depends(current_user)):
        response.headers['Cache-Control']='no-store'
        with db() as conn:
            enabled=conn.execute('SELECT enabled FROM license_settings WHERE singleton').fetchone()['enabled']
            grants=conn.execute('SELECT id,terms,starts_at,ends_at FROM license_grants WHERE user_id=%s AND revoked_at IS NULL AND ends_at>now() ORDER BY starts_at,id',(user['id'],)).fetchall()
            current=next((g for g in grants if g['starts_at']<=datetime.now(timezone.utc)),None)
            usage=conn.execute("""SELECT (SELECT count(*) FROM sponsor_channels WHERE owner_user_id=%s AND archived_at IS NULL) AS channels,
                (SELECT count(*) FROM campaigns WHERE advertiser_user_id=%s AND archived_at IS NULL AND placement_status NOT IN ('completed','cancelled') AND moderation_status<>'rejected') AS open_campaigns,
                (SELECT count(*) FROM license_campaign_usage WHERE grant_id=%s) AS campaigns_per_period""",(user['id'],user['id'],current['id'] if current else None)).fetchone()
            plans=conn.execute('SELECT * FROM license_plans WHERE active ORDER BY price_xtr,days,id').fetchall() if enabled or user['role']=='admin' else []
            orders=conn.execute('SELECT id,plan_id,terms,amount,status,test_mode,created_at,expires_at FROM license_orders WHERE user_id=%s ORDER BY created_at DESC,id DESC LIMIT 20',(user['id'],)).fetchall()
            return dict(enabled=enabled,grants=grants,usage=usage,plans=plans,orders=orders,stars_available=stars_invoices_enabled(),staff_exempt=user['role'] in ('admin','operator'))

    @router.get('/api/v1/admin/licenses')
    def admin_read(admin=Depends(require_admin)):
        with db() as conn:
            return dict(settings=conn.execute('SELECT enabled FROM license_settings WHERE singleton').fetchone(),plans=conn.execute('SELECT * FROM license_plans ORDER BY created_at,id').fetchall())

    @router.put('/api/v1/admin/licenses/settings')
    def settings(data:Settings,admin=Depends(require_admin)):
        with db() as conn:
            conn.execute('SELECT enabled FROM license_settings WHERE singleton FOR UPDATE')
            if data.enabled and not conn.execute('SELECT 1 FROM license_plans WHERE active LIMIT 1').fetchone():raise HTTPException(409,'license_plan_required')
            conn.execute('UPDATE license_settings SET enabled=%s WHERE singleton',(data.enabled,));event(conn,admin['id'],'license_settings_changed',data.model_dump());return data

    @router.post('/api/v1/admin/licenses/plans',status_code=201)
    def create_plan(data:Plan,admin=Depends(require_admin)):
        values=data.model_dump()
        with db() as conn:
            row=conn.execute('INSERT INTO license_plans(name,days,price_xtr,max_channels,max_open_campaigns,max_campaigns_per_period,active) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',tuple(values.values())).fetchone()
            event(conn,admin['id'],'license_plan_created',{'plan_id':row['id'],**values});return row

    @router.put('/api/v1/admin/licenses/plans/{pid}')
    def edit_plan(pid:UUID,data:Plan,admin=Depends(require_admin)):
        with db() as conn:
            enabled=conn.execute('SELECT enabled FROM license_settings WHERE singleton FOR UPDATE').fetchone()['enabled']
            if enabled and not data.active and not conn.execute('SELECT 1 FROM license_plans WHERE active AND id<>%s LIMIT 1',(pid,)).fetchone():raise HTTPException(409,'license_plan_required')
            row=conn.execute('UPDATE license_plans SET name=%s,days=%s,price_xtr=%s,max_channels=%s,max_open_campaigns=%s,max_campaigns_per_period=%s,active=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *',(*data.model_dump().values(),pid)).fetchone()
            if not row:raise HTTPException(404,'license_plan_not_found')
            event(conn,admin['id'],'license_plan_updated',{'plan_id':pid,**data.model_dump()});return row

    @router.post('/api/v1/licenses/checkout')
    def checkout(data:Purchase,user=Depends(current_user)):
        with db() as conn:
            conn.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(user['id'],))
            if not conn.execute('SELECT enabled FROM license_settings WHERE singleton').fetchone()['enabled']:raise HTTPException(409,'license_disabled')
            plan=conn.execute('SELECT * FROM license_plans WHERE id=%s AND active FOR SHARE',(data.plan_id,)).fetchone()
            if not plan:raise HTTPException(404,'license_plan_not_found')
            if plan['revision']!=data.revision:raise HTTPException(409,'license_plan_changed')
            if plan['price_xtr'] and not stars_invoices_enabled():raise HTTPException(409,'stars_checkout_disabled_until_acceptance')
            if not plan['price_xtr'] and conn.execute("SELECT 1 FROM license_orders WHERE user_id=%s AND plan_id=%s AND amount=0 AND status='paid' AND test_mode=%s",(user['id'],plan['id'],is_test_environment())).fetchone():raise HTTPException(409,'license_free_used')
            token,bot_id=stars_checkout.credentials() if plan['price_xtr'] else ('',None)
            order=conn.execute("SELECT * FROM license_orders WHERE user_id=%s AND status='pending' AND (expires_at>now() OR precheckout_at IS NOT NULL) ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(user['id'],)).fetchone()
            if order:
                if order['precheckout_at']:raise HTTPException(409,'stars_payment_processing')
                if order['plan_id']!=plan['id'] or order['terms']['revision']!=plan['revision']:raise HTTPException(409,'license_pending_invoice')
                if order['test_mode']!=is_test_environment() or order['bot_id']!=bot_id:raise HTTPException(409,'stars_environment_changed')
            else:
                terms={key:plan[key] for key in ('name','days','price_xtr','max_channels','max_open_campaigns','max_campaigns_per_period','revision')}
                order=conn.execute('INSERT INTO license_orders(user_id,plan_id,terms,amount,test_mode,bot_id) VALUES(%s,%s,%s::jsonb,%s,%s,%s) RETURNING *',(user['id'],plan['id'],json.dumps(terms),plan['price_xtr'],is_test_environment(),bot_id)).fetchone()
            if not plan['price_xtr']:
                conn.execute("UPDATE license_orders SET status='paid',paid_at=now() WHERE id=%s",(order['id'],));activate(conn,order)
                return {'status':'paid','test_mode':order['test_mode']}
            url=order['invoice_url']
            if not url:
                prepared=SimpleNamespace(title='Лицензия puShok',price_label='Лицензия кабинета',description=plan['name']+' · '+str(plan['days'])+' дн.',invoice_payload='license:'+str(order['id']),stars_amount=order['amount'])
                try:url=stars_checkout.create_invoice_link(token,prepared)
                except stars_checkout.StarsLinkError:raise HTTPException(503,'stars_link_unavailable') from None
                conn.execute('UPDATE license_orders SET invoice_url=%s WHERE id=%s',(url,order['id']))
            return {'status':'pending','invoice_url':url,'order_id':order['id'],'amount_xtr':order['amount']}

    @router.post('/api/v1/licenses/orders/{oid}/resume')
    def resume(oid:UUID,user=Depends(current_user)):
        with db() as conn:
            order=locked_order(conn,oid)
            if not order or order['user_id']!=user['id']:raise HTTPException(404,'license_order_not_found')
            if order['status']!='pending' or order['expires_at']<=datetime.now(timezone.utc):raise HTTPException(409,'license_invoice_expired')
            if order['precheckout_at']:raise HTTPException(409,'stars_payment_processing')
            if order['test_mode']!=is_test_environment() or order['bot_id']!=stars_checkout.credentials()[1]:raise HTTPException(409,'stars_environment_changed')
            if not stars_invoices_enabled() or not conn.execute('SELECT enabled FROM license_settings WHERE singleton').fetchone()['enabled']:raise HTTPException(409,'license_disabled')
            return {'invoice_url':order['invoice_url']}

    @router.post('/api/v1/licenses/orders/{oid}/close')
    def close(oid:UUID,user=Depends(current_user)):
        with db() as conn:
            order=locked_order(conn,oid)
            if not order or order['user_id']!=user['id']:raise HTTPException(404,'license_order_not_found')
            if order['status']=='pending' and order['precheckout_at'] is None:conn.execute("UPDATE license_orders SET status='closed' WHERE id=%s",(oid,))
            elif order['status']!='closed':raise HTTPException(409,'stars_payment_processing')
            return {'closed':True}

    @router.post('/api/v1/internal/bot/license/precheckout',include_in_schema=False)
    def precheckout(data:StarsPrecheckoutInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db() as conn:
            order=locked_order(conn,parse_order(data.invoice_payload))
            if not (stars_invoices_enabled() and contract(order,data) and order['status']=='pending' and order['expires_at']>datetime.now(timezone.utc)
                    and order['precheckout_id'] in (None,data.query_id) and conn.execute('SELECT enabled FROM license_settings WHERE singleton').fetchone()['enabled']):return {'ok':False}
            conn.execute('UPDATE license_orders SET precheckout_id=%s,precheckout_at=COALESCE(precheckout_at,now()) WHERE id=%s',(data.query_id,order['id']))
            return {'ok':True}

    @router.post('/api/v1/internal/bot/license/settle',include_in_schema=False)
    def settle(data:StarsReceiptInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('license-charge:'+data.telegram_payment_charge_id,))
            order=locked_order(conn,parse_order(data.invoice_payload))
            conn.execute("""INSERT INTO stars_payment_receipts(telegram_payment_charge_id,payer_telegram_user_id,currency,total_amount,license_order_id)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(telegram_payment_charge_id) DO NOTHING""",(data.telegram_payment_charge_id,data.telegram_user_id,data.currency,data.total_amount,order['id'] if order else None))
            receipt=conn.execute('SELECT * FROM stars_payment_receipts WHERE telegram_payment_charge_id=%s FOR UPDATE',(data.telegram_payment_charge_id,)).fetchone()
            same=bool(order and receipt['license_order_id']==order['id'] and receipt['payer_telegram_user_id']==data.telegram_user_id and receipt['total_amount']==data.total_amount and receipt['currency'].strip()==data.currency)
            if same and order['charge_id']==data.telegram_payment_charge_id and order['status'] in ('paid','refunded'):return {'status':'already_processed','test_mode':order['test_mode']}
            if not (same and contract(order,data) and order['status']=='pending' and order['precheckout_at']):
                if receipt['handling_status']!='applied':conn.execute("UPDATE stars_payment_receipts SET handling_status='requires_review',reason_code='license_contract_mismatch',processed_at=now() WHERE id=%s",(receipt['id'],))
                return {'status':'requires_review'}
            conn.execute("UPDATE license_orders SET status='paid',paid_at=now(),charge_id=%s WHERE id=%s",(data.telegram_payment_charge_id,order['id']))
            order['charge_id']=data.telegram_payment_charge_id
            if not apply_refund(conn,order):activate(conn,order)
            conn.execute("UPDATE stars_payment_receipts SET handling_status='applied',processed_at=now() WHERE id=%s",(receipt['id'],))
            return {'status':'applied','test_mode':order['test_mode']}

    @router.post('/api/v1/internal/bot/license/refund',include_in_schema=False)
    def refund(data:Refund,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('license-charge:'+data.telegram_payment_charge_id,))
            conn.execute('INSERT INTO license_refunds(charge_id,telegram_user_id,amount,bot_id,test_mode) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(data.telegram_payment_charge_id,data.telegram_user_id,data.total_amount,stars_checkout.credentials()[1],is_test_environment()))
            found=conn.execute('SELECT id FROM license_orders WHERE charge_id=%s',(data.telegram_payment_charge_id,)).fetchone()
            if found:apply_refund(conn,locked_order(conn,found['id']))
            return {'status':'recorded'}
    return router
