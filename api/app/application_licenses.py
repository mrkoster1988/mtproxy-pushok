"""Центр продажи лицензий установок и клиент с проверкой подписи продавца."""
from contextlib import contextmanager
from datetime import timedelta
import json
import os
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request,build_opener,HTTPRedirectHandler
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter,Depends,Header,HTTPException,Response
from pydantic import BaseModel,ConfigDict,Field,field_validator
from . import license_crypto as crypto,stars_checkout
from .bot_stars import StarsPrecheckoutInput,StarsReceiptInput,authorize
from .payment_gate import stars_invoices_enabled
from .telegram_environment import is_test_environment

LIMITS=('max_nodes','max_admins','max_campaigns')
class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Plan(Input):
    name:str=Field(min_length=1,max_length=80)
    days:int=Field(ge=1,le=3650,strict=True)
    price_xtr:int=Field(ge=1,le=1000000,strict=True)
    max_nodes:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    max_admins:int|None=Field(default=None,ge=1,le=1000000,strict=True)
    max_campaigns:int|None=Field(default=None,ge=0,le=1000000,strict=True)
    active:bool=Field(default=False,strict=True)
    @field_validator('name')
    @classmethod
    def trimmed(cls,v):
        if not v.strip():raise ValueError('name_required')
        return v.strip()
class Settings(Input):enabled:bool=Field(strict=True)
class Purchase(Input):
    plan_id:UUID
    revision:int=Field(ge=1,strict=True)
class Signed(Input):
    payload:dict
    signature:str=Field(min_length=86,max_length=86)
class StoreRequest(Input):
    installation_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    public_key:str=Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    operation:str=Field(pattern=r'^(catalog|status|checkout|close)$')
    nonce:str=Field(pattern=r'^[0-9a-f]{32}$')
    issued_at:int=Field(strict=True)
    payer_id:int|None=Field(default=None,gt=0,strict=True)
    plan_id:UUID|None=None
    revision:int|None=Field(default=None,ge=1,strict=True)
    order_id:UUID|None=None
class Refund(Input):
    telegram_user_id:int=Field(gt=0,strict=True)
    currency:str=Field(pattern='^XTR$')
    total_amount:int=Field(gt=0,le=1000000,strict=True)
    telegram_payment_charge_id:str=Field(min_length=1,max_length=255)


def mode():
    value=os.getenv('PUSHOK_LICENSE_MODE','disabled')
    if value not in ('disabled','seller','client'):raise HTTPException(503,'invalid_license_mode')
    return value

def seller():
    if mode()!='seller':raise HTTPException(404,'license_store_unavailable')

@contextmanager
def db(dsn):
    try:
        with psycopg.connect(dsn,connect_timeout=3,row_factory=dict_row,options='-c lock_timeout=5000 -c statement_timeout=15000') as conn:yield conn
    except psycopg.Error as exc:raise HTTPException(503,'database_unavailable') from exc


def public_key():return crypto.b64(crypto.key('seller').public_key().public_bytes_raw())
def parse_order(payload):
    try:
        uid=UUID(payload[8:]);return uid if payload=='license:'+str(uid) else None
    except (ValueError,TypeError,AttributeError):return None

def lock_order(conn,oid):
    row=conn.execute('SELECT installation_id FROM app_license_orders WHERE id=%s',(oid,)).fetchone()
    if not row:return None
    conn.execute('SELECT id FROM app_license_installations WHERE id=%s FOR UPDATE',(row['installation_id'],))
    return conn.execute('SELECT * FROM app_license_orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()

def contract(order,data):
    return bool(order and order['payer_id']==data.telegram_user_id and data.currency=='XTR' and data.total_amount==order['amount'] and order['test_mode']==is_test_environment() and order['bot_id']==stars_checkout.credentials()[1])

def apply_refund(conn,order):
    r=conn.execute('SELECT * FROM app_license_refunds WHERE charge_id=%s',(order['charge_id'],)).fetchone()
    if not r or any(r[k]!=order[k] for k in ('payer_id','amount','bot_id','test_mode')):return False
    conn.execute("UPDATE app_license_orders SET status='refunded' WHERE id=%s",(order['id'],))
    conn.execute('UPDATE app_license_grants SET revoked_at=COALESCE(revoked_at,now()) WHERE order_id=%s',(order['id'],))
    conn.execute('UPDATE app_license_refunds SET applied=true WHERE charge_id=%s',(order['charge_id'],));return True

def checkout(conn,p):
    if not conn.execute('SELECT enabled FROM app_license_settings WHERE singleton').fetchone()['enabled']:raise HTTPException(409,'license_sales_disabled')
    if not stars_invoices_enabled():raise HTTPException(409,'stars_checkout_disabled_until_acceptance')
    if not p.payer_id or not p.plan_id or not p.revision:raise HTTPException(422,'license_order_incomplete')
    plan=conn.execute('SELECT * FROM app_license_plans WHERE id=%s AND active FOR SHARE',(p.plan_id,)).fetchone()
    if not plan:raise HTTPException(404,'license_plan_not_found')
    if plan['revision']!=p.revision:raise HTTPException(409,'license_plan_changed')
    token,botid=stars_checkout.credentials()
    order=conn.execute("SELECT * FROM app_license_orders WHERE installation_id=%s AND status='pending' AND (expires_at>now() OR precheckout_at IS NOT NULL) ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(p.installation_id,)).fetchone()
    if order:
        if order['precheckout_at']:raise HTTPException(409,'stars_payment_processing')
        if order['payer_id']!=p.payer_id or order['plan_id']!=p.plan_id or order['terms']['revision']!=p.revision:raise HTTPException(409,'license_pending_invoice')
        if order['bot_id']!=botid or order['test_mode']!=is_test_environment():raise HTTPException(409,'stars_environment_changed')
    else:
        terms={k:plan[k] for k in ('name','days','price_xtr','revision',*LIMITS)}
        order=conn.execute('INSERT INTO app_license_orders(installation_id,payer_id,plan_id,terms,amount,test_mode,bot_id) VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s) RETURNING *',(p.installation_id,p.payer_id,p.plan_id,json.dumps(terms),plan['price_xtr'],is_test_environment(),botid)).fetchone()
    url=order['invoice_url']
    if not url:
        prepared=SimpleNamespace(title='Лицензия приложения puShok',description=plan['name']+' · '+str(plan['days'])+' дн.',price_label='Лицензия приложения',invoice_payload='license:'+str(order['id']),stars_amount=order['amount'])
        try:url=stars_checkout.create_invoice_link(token,prepared)
        except stars_checkout.StarsLinkError:raise HTTPException(503,'stars_link_unavailable') from None
        conn.execute('UPDATE app_license_orders SET invoice_url=%s WHERE id=%s',(url,order['id']))
    return {'invoice_url':url,'order_id':str(order['id']),'amount_xtr':order['amount'],'test_mode':order['test_mode']}


def status_for(conn,iid):
    grants=conn.execute('SELECT id,terms,extract(epoch FROM starts_at)::BIGINT AS starts_at,extract(epoch FROM ends_at)::BIGINT AS ends_at FROM app_license_grants WHERE installation_id=%s AND revoked_at IS NULL AND ends_at>now() ORDER BY starts_at,id',(iid,)).fetchall()
    orders=conn.execute('SELECT id,terms,amount,status,test_mode,created_at,expires_at,precheckout_at,invoice_url FROM app_license_orders WHERE installation_id=%s ORDER BY created_at DESC,id DESC LIMIT 20',(iid,)).fetchall()
    return {'grants':[dict(g,id=str(g['id'])) for g in grants],'orders':json.loads(json.dumps(orders,default=str)),'lease_until':int(time.time())+72*3600}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def remote(envelope):
    base=os.getenv('PUSHOK_LICENSE_SERVER','').rstrip('/');parsed=urlparse(base)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise HTTPException(503,'license_server_not_configured')
    trusted=os.getenv('PUSHOK_LICENSE_PUBLIC_KEY','')
    try:
        if len(crypto.raw(trusted))!=32:raise ValueError()
    except ValueError:raise HTTPException(503,'license_key_not_configured') from None
    try:
        req=Request(base+'/api/v1/license-store/requests',data=crypto.canonical(envelope),headers={'Content-Type':'application/json'},method='POST')
        with build_opener(NoRedirect).open(req,timeout=8) as response:raw=response.read(262145)
        if len(raw)>262144:raise ValueError()
        result=json.loads(raw);payload=crypto.verify(trusted,result)
        original=envelope['payload']
        if any(payload[k]!=original[k] for k in ('installation_id','nonce','operation')) or abs(time.time()-payload['issued_at'])>120:raise ValueError()
        return result
    except HTTPError as exc:
        try:detail=json.loads(exc.read(8192)).get('detail')
        except (ValueError,TypeError,AttributeError):detail=None
        known={'license_plan_changed','license_plan_not_found','license_pending_invoice','license_sales_disabled','stars_payment_processing','stars_environment_changed','stars_checkout_disabled_until_acceptance','license_order_not_found'}
        if exc.code in (404,409) and detail in known:raise HTTPException(exc.code,detail) from None
        raise HTTPException(503,'license_server_unavailable') from None
    except (OSError,ValueError,TypeError,KeyError):raise HTTPException(503,'license_server_unavailable') from None

def sync(dsn):
    if mode()!='client':return
    envelope=remote(crypto.request('status'));p=envelope['payload']
    # Ограниченная аренда подписанного кэша не позволяет бессрочно игнорировать возврат.
    if type(p.get('lease_until')) is not int or p['lease_until']>int(time.time())+72*3600+120 or not isinstance(p.get('grants'),list):raise HTTPException(503,'invalid_license_response')
    for g in p['grants']:
        if type(g.get('starts_at')) is not int or type(g.get('ends_at')) is not int or g['ends_at']<=g['starts_at']:raise HTTPException(503,'invalid_license_response')
        try:Plan.model_validate({k:v for k,v in g['terms'].items() if k!='revision'})
        except (ValueError,TypeError,KeyError,AttributeError):raise HTTPException(503,'invalid_license_response') from None
    with db(dsn) as conn:conn.execute('UPDATE app_license_state SET envelope=%s::jsonb,checked_at=now(),last_error=NULL WHERE singleton',(json.dumps(envelope),))

def start_client(dsn):
    selected=mode();iid=None
    if selected=='disabled':return threading.Event()
    if selected=='client':iid,_=crypto.identity()
    with db(dsn) as conn:
        old=conn.execute('SELECT * FROM app_license_state WHERE singleton FOR UPDATE').fetchone()
        valid=False
        if old['envelope'] and iid==old['installation_id']:
            try:valid=crypto.verify(os.getenv('PUSHOK_LICENSE_PUBLIC_KEY',''),old['envelope'])['installation_id']==iid
            except (ValueError,KeyError):pass
        conn.execute('UPDATE app_license_state SET required=%s,installation_id=%s,envelope=CASE WHEN %s THEN envelope ELSE NULL END WHERE singleton',(selected=='client',iid,valid))
    if selected=='seller':crypto.key('seller')
    stop=threading.Event()
    def worker():
        while not stop.is_set():
            try:sync(dsn)
            except Exception:
                # Никогда не выводим URL, ключ или подписанный запрос в журнал ошибки.
                try:
                    with db(dsn) as conn:conn.execute("UPDATE app_license_state SET last_error='license_server_unavailable' WHERE singleton")
                except Exception:pass
            stop.wait(60)
    if selected=='client':threading.Thread(target=worker,name='license-refresh',daemon=True).start()
    return stop


def router_for(get_dsn,current_user,require_admin):
    router=APIRouter()
    @router.post('/api/v1/license-store/requests')
    def store_request(data:Signed,response:Response):
        seller();response.headers['Cache-Control']='no-store'
        try:
            p=StoreRequest.model_validate(crypto.check_request(data.model_dump()))
        except (ValueError,KeyError,TypeError):raise HTTPException(403,'license_request_invalid') from None
        with db(get_dsn()) as conn:
            if p.operation=='catalog':
                enabled=conn.execute('SELECT enabled FROM app_license_settings WHERE singleton').fetchone()['enabled']
                plans=conn.execute('SELECT * FROM app_license_plans WHERE active ORDER BY price_xtr,days,id').fetchall() if enabled else []
                result={'plans':json.loads(json.dumps(plans,default=str)),'available':enabled and stars_invoices_enabled()}
            elif p.operation=='status':result=status_for(conn,p.installation_id)
            else:
                conn.execute('INSERT INTO app_license_installations(id,public_key) VALUES(%s,%s) ON CONFLICT DO NOTHING',(p.installation_id,p.public_key))
                conn.execute('SELECT id FROM app_license_installations WHERE id=%s FOR UPDATE',(p.installation_id,))
                if p.operation=='checkout':result=checkout(conn,p)
                else:
                    order=conn.execute('SELECT * FROM app_license_orders WHERE id=%s AND installation_id=%s FOR UPDATE',(p.order_id,p.installation_id)).fetchone()
                    if not order:raise HTTPException(404,'license_order_not_found')
                    if order['status']=='pending' and not order['precheckout_at']:conn.execute("UPDATE app_license_orders SET status='closed' WHERE id=%s",(order['id'],))
                    elif order['status']!='closed':raise HTTPException(409,'stars_payment_processing')
                    result={'closed':True}
        return crypto.sign(crypto.key('seller'),dict(result,installation_id=p.installation_id,operation=p.operation,nonce=p.nonce,issued_at=int(time.time())))

    @router.get('/api/v1/admin/license')
    def own(response:Response,admin=Depends(require_admin)):
        response.headers['Cache-Control']='no-store';selected=mode()
        with db(get_dsn()) as conn:
            if selected=='seller':return {'mode':selected,'public_key':public_key(),'settings':conn.execute('SELECT enabled FROM app_license_settings').fetchone(),'plans':conn.execute('SELECT * FROM app_license_plans ORDER BY created_at,id').fetchall(),'orders':conn.execute('SELECT id,installation_id,payer_id,terms,amount,status,test_mode,created_at FROM app_license_orders ORDER BY created_at DESC LIMIT 50').fetchall()}
            state=conn.execute('SELECT * FROM app_license_state WHERE singleton').fetchone()
            usage=conn.execute("SELECT (SELECT count(*) FROM proxy_nodes WHERE archived_at IS NULL) AS max_nodes,(SELECT count(*) FROM users WHERE role='admin') AS max_admins,(SELECT count(*) FROM campaigns WHERE archived_at IS NULL AND placement_status NOT IN ('completed','cancelled') AND moderation_status<>'rejected') AS max_campaigns").fetchone()
        data={'mode':selected,'usage':usage,'state':state,'plans':[],'available':False}
        if selected=='client':
            try:data.update({k:v for k,v in remote(crypto.request('catalog'))['payload'].items() if k in ('plans','available')})
            except HTTPException as exc:data['error']=exc.detail
        return data

    @router.get('/api/v1/admin/license/status')
    def cached_status(response:Response,admin=Depends(require_admin)):
        response.headers['Cache-Control']='no-store'
        with db(get_dsn()) as conn:
            row=conn.execute('SELECT envelope,checked_at,last_error FROM app_license_state WHERE singleton').fetchone()
        return row

    @router.put('/api/v1/admin/license/settings')
    def settings(data:Settings,admin=Depends(require_admin)):
        seller()
        with db(get_dsn()) as conn:
            conn.execute('SELECT enabled FROM app_license_settings WHERE singleton FOR UPDATE')
            if data.enabled and not conn.execute('SELECT 1 FROM app_license_plans WHERE active LIMIT 1').fetchone():raise HTTPException(409,'license_plan_required')
            conn.execute('UPDATE app_license_settings SET enabled=%s WHERE singleton',(data.enabled,));return data

    @router.post('/api/v1/admin/license/plans',status_code=201)
    def create_plan(data:Plan,admin=Depends(require_admin)):
        seller()
        with db(get_dsn()) as conn:return conn.execute('INSERT INTO app_license_plans(name,days,price_xtr,max_nodes,max_admins,max_campaigns,active) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',tuple(data.model_dump().values())).fetchone()

    @router.put('/api/v1/admin/license/plans/{pid}')
    def edit_plan(pid:UUID,data:Plan,admin=Depends(require_admin)):
        seller()
        with db(get_dsn()) as conn:
            enabled=conn.execute('SELECT enabled FROM app_license_settings WHERE singleton FOR UPDATE').fetchone()['enabled']
            if enabled and not data.active and not conn.execute('SELECT 1 FROM app_license_plans WHERE active AND id<>%s',(pid,)).fetchone():raise HTTPException(409,'license_plan_required')
            row=conn.execute('UPDATE app_license_plans SET name=%s,days=%s,price_xtr=%s,max_nodes=%s,max_admins=%s,max_campaigns=%s,active=%s,revision=revision+1 WHERE id=%s RETURNING *',(*data.model_dump().values(),pid)).fetchone()
            if not row:raise HTTPException(404,'license_plan_not_found')
            return row

    @router.post('/api/v1/admin/license/checkout')
    def buy(data:Purchase,admin=Depends(require_admin)):
        if mode()!='client':raise HTTPException(409,'license_client_required')
        with db(get_dsn()) as conn:payer=conn.execute('SELECT telegram_user_id FROM users WHERE id=%s',(admin['id'],)).fetchone()['telegram_user_id']
        return remote(crypto.request('checkout',payer_id=payer,plan_id=str(data.plan_id),revision=data.revision))['payload']

    @router.post('/api/v1/admin/license/sync')
    def refresh(admin=Depends(require_admin)):
        sync(get_dsn());return {'updated':True}

    @router.post('/api/v1/admin/license/orders/{oid}/close')
    def close(oid:UUID,admin=Depends(require_admin)):
        if mode()!='client':raise HTTPException(409,'license_client_required')
        remote(crypto.request('close',order_id=str(oid)));sync(get_dsn());return {'closed':True}

    @router.post('/api/v1/internal/bot/license/precheckout',include_in_schema=False)
    def precheckout(data:StarsPrecheckoutInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        seller();authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db(get_dsn()) as conn:
            order=lock_order(conn,parse_order(data.invoice_payload))
            ok=bool(order and stars_invoices_enabled() and contract(order,data) and order['status']=='pending' and order['expires_at'].timestamp()>time.time() and order['precheckout_id'] in (None,data.query_id) and conn.execute('SELECT enabled FROM app_license_settings').fetchone()['enabled'])
            if ok:conn.execute('UPDATE app_license_orders SET precheckout_id=%s,precheckout_at=COALESCE(precheckout_at,now()) WHERE id=%s',(data.query_id,order['id']))
            return {'ok':ok}

    @router.post('/api/v1/internal/bot/license/settle',include_in_schema=False)
    def settle(data:StarsReceiptInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        seller();authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db(get_dsn()) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('license-charge:'+data.telegram_payment_charge_id,))
            order=lock_order(conn,parse_order(data.invoice_payload))
            conn.execute('INSERT INTO stars_payment_receipts(telegram_payment_charge_id,payer_telegram_user_id,currency,total_amount,app_license_order_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(data.telegram_payment_charge_id,data.telegram_user_id,data.currency,data.total_amount,order['id'] if order else None))
            receipt=conn.execute('SELECT * FROM stars_payment_receipts WHERE telegram_payment_charge_id=%s FOR UPDATE',(data.telegram_payment_charge_id,)).fetchone()
            same=bool(order and receipt['app_license_order_id']==order['id'] and receipt['payer_telegram_user_id']==data.telegram_user_id and receipt['currency'].strip()==data.currency and receipt['total_amount']==data.total_amount)
            if same and order['charge_id']==data.telegram_payment_charge_id and order['status'] in ('paid','refunded'):return {'status':'already_processed','test_mode':order['test_mode']}
            if not(same and contract(order,data) and order['status']=='pending' and order['precheckout_at']):
                if receipt['handling_status']!='applied':conn.execute("UPDATE stars_payment_receipts SET handling_status='requires_review',reason_code='application_license_contract',processed_at=now() WHERE id=%s",(receipt['id'],))
                return {'status':'requires_review'}
            conn.execute("UPDATE app_license_orders SET status='paid',charge_id=%s WHERE id=%s",(data.telegram_payment_charge_id,order['id']));order['charge_id']=data.telegram_payment_charge_id
            if not apply_refund(conn,order) and not order['test_mode']:
                start=conn.execute('SELECT greatest(now(),coalesce(max(ends_at),now())) AS start FROM app_license_grants WHERE installation_id=%s AND revoked_at IS NULL',(order['installation_id'],)).fetchone()['start']
                conn.execute('INSERT INTO app_license_grants(order_id,installation_id,terms,starts_at,ends_at) VALUES(%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING',(order['id'],order['installation_id'],json.dumps(order['terms']),start,start+timedelta(days=order['terms']['days'])))
            conn.execute("UPDATE stars_payment_receipts SET handling_status='applied',processed_at=now() WHERE id=%s",(receipt['id'],))
            return {'status':'applied','test_mode':order['test_mode']}

    @router.post('/api/v1/internal/bot/license/refund',include_in_schema=False)
    def refund(data:Refund,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        seller();authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        with db(get_dsn()) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('license-charge:'+data.telegram_payment_charge_id,))
            conn.execute('INSERT INTO app_license_refunds(charge_id,payer_id,amount,bot_id,test_mode) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(data.telegram_payment_charge_id,data.telegram_user_id,data.total_amount,stars_checkout.credentials()[1],is_test_environment()))
            row=conn.execute('SELECT id FROM app_license_orders WHERE charge_id=%s',(data.telegram_payment_charge_id,)).fetchone()
            if row:apply_refund(conn,lock_order(conn,row['id']))
            return {'status':'recorded'}
    return router
