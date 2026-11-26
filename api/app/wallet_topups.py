"""Пополнения: доверенные подтверждения провайдеров и повторяемые операции."""
import os
import re
from . import wallet_acquirers as acquirers
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException
from . import wallet_ledger as ledger, wallet_controls as controls, sbp_provider, stars_checkout
from .sbp_checkout import settings as sbp_settings
from .sbp_verification import validate_sbp_identity, SBPVerificationError
from .payment_gate import stars_invoices_enabled
from .telegram_environment import is_test_environment


def connect(dsn):
    return psycopg.connect(dsn,connect_timeout=3,row_factory=dict_row,
                           options='-c lock_timeout=5000 -c statement_timeout=20000')


def view(row):
    return {k:row[k] for k in ('id','provider','currency','amount','status','invoice_url','test_mode',
            'refunded_amount','created_at','settled_at','review_reason')}


def parse_payload(value):
    try:
        uid=UUID(value[7:])
        return uid if value=='wallet:'+str(uid) else None
    except (ValueError,TypeError,AttributeError):
        return None


from . import control

def create(dsn,user,data):
    if data.currency=='RUB':
        with control.locked():
            return _create(dsn,user,data)
    return _create(dsn,user,data)


def _create(dsn,user,data):
    currency=data.currency
    if currency == 'XTR':
        raise HTTPException(409, 'wallet_stars_topup_retired')
    if currency=='RUB' and user.get('login_surface')=='telegram':
        raise HTTPException(409,'wallet_stars_only_in_telegram')
    provider = getattr(data, 'provider', 'sbp')
    cfg = acquirers.settings(provider) if provider in acquirers.MINIMUM else sbp_settings()
    if not cfg['enabled']:
        raise HTTPException(409, 'wallet_method_unavailable' if provider in acquirers.MINIMUM else 'sbp_checkout_disabled_until_acceptance')
    if provider in acquirers.MINIMUM:
        if not acquirers.MINIMUM[provider] <= data.amount <= acquirers.MAXIMUM:
            raise HTTPException(422, 'wallet_method_amount')
    elif data.amount < 100:
        raise HTTPException(422, 'sbp_invalid_amount')
    email = getattr(data, 'receipt_email', None)
    if provider == 'antilopay' and not email:
        raise HTTPException(422, 'wallet_receipt_email')
    account, test = cfg['shop'], cfg['test_mode']
    host = cfg['host']
    if host and not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
        raise HTTPException(409, 'wallet_method_unavailable')
    # Ключ сохраняется до внешнего вызова; неопределённый результат повторяем тем же ключом.
    with connect(dsn) as conn:
        controls.require_enabled(conn)
        # Один незавершённый счёт на пользователя/валюту, даже после очистки localStorage.
        conn.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(user['id'],))
        conn.execute("UPDATE wallet_topups SET status='failed',invoice_url=NULL WHERE user_id=%s AND provider='telegram_stars' AND status='pending' AND expires_at<=now() AND precheckout_approved_at IS NULL",(user['id'],))
        row=conn.execute('SELECT * FROM wallet_topups WHERE user_id=%s AND request_id=%s',(user['id'],data.request_id)).fetchone()
        if not row:
            if conn.execute("SELECT 1 FROM wallet_topups WHERE user_id=%s AND currency=%s AND status IN ('created','pending','review')",(user['id'],currency)).fetchone():
                raise HTTPException(409,'wallet_topup_in_progress')
            row=conn.execute('''INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode,receipt_email)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (user['id'],data.request_id,provider,currency,data.amount,account,test,email if provider == 'antilopay' else None)).fetchone()
            if host:
                conn.execute('UPDATE wallet_topups SET return_url=%s WHERE id=%s',
                    ('https://'+cfg['host']+'/?wallet_topup='+str(row['id']),row['id']))
        elif row['currency']!=currency or row['amount']!=data.amount or row['provider']!=provider:
            raise HTTPException(409,'wallet_request_conflict')
    return resume(dsn,row['id'],user)


def resume(dsn,tid,user):
    with connect(dsn) as conn:
        controls.require_enabled(conn)
        row=conn.execute('SELECT * FROM wallet_topups WHERE id=%s AND user_id=%s FOR UPDATE',(tid,user['id'])).fetchone()
        if not row:raise HTTPException(404,'wallet_topup_not_found')
        if row['currency'] == 'RUB' and user.get('login_surface') == 'telegram':
            raise HTTPException(409, 'wallet_stars_only_in_telegram')
        if row['status']!='created':return view(row)
        if row['provider'] in acquirers.MINIMUM:
            cfg = matching_acquirer(row)
            if not cfg['enabled']:
                raise HTTPException(409, 'wallet_method_unavailable')
            try:
                pid, url = acquirers.create(row, cfg)
            except acquirers.InvoiceRejected:
                rejected = conn.execute("""UPDATE wallet_topups SET status='failed',
                    receipt_email=NULL,review_reason='invoice_rejected' WHERE id=%s RETURNING *""", (tid,)).fetchone()
                return view(rejected)
            row = conn.execute("""UPDATE wallet_topups SET status='pending',provider_payment_id=%s,
                invoice_url=%s,receipt_email=NULL WHERE id=%s RETURNING *""", (pid, url, tid)).fetchone()
        elif row['currency']=='RUB':
            cfg=sbp_settings()
            if (not cfg['enabled'] or row['provider_account']!=cfg['shop'] or row['test_mode']!=cfg['test_mode']
                or row['return_url']!='https://'+cfg['host']+'/?wallet_topup='+str(row['id'])):
                raise HTTPException(409,'sbp_configuration_changed')
            if datetime.now(timezone.utc)-row['created_at']>=timedelta(hours=23):
                conn.execute("UPDATE wallet_topups SET status='review',review_reason='creation_window_expired' WHERE id=%s",(tid,))
                row.update(status='review',review_reason='creation_window_expired');return view(row)
            result=sbp_provider.create_sbp_payment(campaign_id=str(tid),amount_kopecks=row['amount'],
                idempotence_key=str(tid),return_url=row['return_url'],expected_test=row['test_mode'],wallet_topup=True)
            row=conn.execute("UPDATE wallet_topups SET status='pending',provider_payment_id=%s,invoice_url=%s WHERE id=%s RETURNING *",
                             (result['payment_id'],result['confirmation_url'],tid)).fetchone()
        else:
            token,bot_id=stars_checkout.credentials()
            if not stars_invoices_enabled():raise HTTPException(409,'stars_checkout_disabled_until_acceptance')
            if row['provider_account']!=str(bot_id) or row['test_mode']!=is_test_environment():
                raise HTTPException(409,'stars_environment_changed')
            prepared=SimpleNamespace(title='Баланс puShok',description='Пополнение для рекламных размещений',
                invoice_payload='wallet:'+str(tid),stars_amount=row['amount'])
            url=stars_checkout.create_invoice_link(token,prepared)
            row=conn.execute("UPDATE wallet_topups SET status='pending',invoice_url=%s,expires_at=now()+interval '30 minutes' WHERE id=%s RETURNING *",(url,tid)).fetchone()
        return view(row)


def refund_amount(payment,maximum):
    raw=payment.get('refunded_amount',{'value':'0.00','currency':'RUB'})
    if not isinstance(raw,dict) or raw.get('currency')!='RUB' or not isinstance(raw.get('value'),str):
        raise SBPVerificationError('invalid_refund')
    try:value=Decimal(raw['value'])
    except InvalidOperation:raise SBPVerificationError('invalid_refund') from None
    if not value.is_finite() or value.as_tuple().exponent!=-2 or not 0<=value*100<=maximum:
        raise SBPVerificationError('invalid_refund')
    return int(value*100)


def matching_acquirer(row):
    cfg = acquirers.settings(row['provider'])
    if not cfg['configured'] or cfg['shop'] != row['provider_account'] or cfg['test_mode'] != row['test_mode']:
        raise HTTPException(409, 'sbp_configuration_changed')
    return cfg


def record_paid(conn, row, refunded, reason):
    if refunded<row['refunded_amount']:raise SBPVerificationError('refund_amount_decreased')
    if row['status'] not in ('pending','succeeded','refunded'):raise SBPVerificationError('payment_state_conflict')
    if not row['test_mode']:
        ledger.post(conn,row['user_id'],'RUB',row['amount'],'topup','topup:'+str(row['id']),
                    reason,topup=row['id'])
        if refunded>row['refunded_amount']:
            ledger.post(conn,row['user_id'],'RUB',row['refunded_amount']-refunded,'provider_refund',
                'provider-refund:'+str(row['id'])+':'+str(refunded),'Возврат платёжной системой',topup=row['id'])
    conn.execute("""UPDATE wallet_topups SET status=%s,settled_at=coalesce(settled_at,now()),refunded_amount=%s,
        invoice_url=NULL,check_failures=0,review_reason=NULL,next_check_at=now()+interval '1 hour' WHERE id=%s""",
        ('refunded' if refunded==row['amount'] else 'succeeded',refunded,row['id']))


def verify_acquirer(conn, row):
    state, refunded = acquirers.inspect_payment(row, matching_acquirer(row))
    if state == 'paid':
        reason = 'Пополнение через Platega' if row['provider'] == 'platega' else 'Пополнение через СБП · Antilopay'
        record_paid(conn, row, refunded, reason)
    elif state == 'review':
        conn.execute("""UPDATE wallet_topups SET review_reason='provider_dispute_or_partial_refund',
            next_check_at=now()+interval '1 hour',invoice_url=NULL WHERE id=%s""", (row['id'],))
    elif row['status'] != 'pending':
        raise SBPVerificationError('payment_state_conflict')
    else:
        conn.execute("""UPDATE wallet_topups SET status=%s, review_reason=NULL, check_failures=0,
            invoice_url=CASE WHEN %s='failed' THEN NULL ELSE invoice_url END,
            next_check_at=now()+interval '1 minute' WHERE id=%s""", (state,state,row['id']))


def settle_sbp(conn,row,payment):
    validate_sbp_identity(payment,expected_payment_id=row['provider_payment_id'],expected_campaign_id=str(row['id']),
        expected_kopecks=row['amount'],expected_test=row['test_mode'],wallet_topup=True)
    if payment.get('status')=='succeeded' and payment.get('paid') is True and payment.get('captured_at'):
        refunded=refund_amount(payment,row['amount'])
        record_paid(conn, row, refunded, 'Пополнение через СБП')
    elif payment.get('status')=='canceled' and payment.get('paid') is False and row['status']=='pending':
        conn.execute("UPDATE wallet_topups SET status='failed',invoice_url=NULL WHERE id=%s",(row['id'],))
    elif payment.get('status')=='pending' and row['status']=='pending':
        conn.execute("UPDATE wallet_topups SET next_check_at=now()+interval '1 minute',check_failures=0,review_reason=NULL WHERE id=%s",(row['id'],))
    else:raise SBPVerificationError('payment_state_conflict')


def verify(dsn,tid,uid):
    with connect(dsn) as conn:
        row=conn.execute('SELECT * FROM wallet_topups WHERE id=%s AND user_id=%s FOR UPDATE',(tid,uid)).fetchone()
        if not row:raise HTTPException(404,'wallet_topup_not_found')
        if row['provider']=='telegram_stars':
            if (row['status']=='pending' and not row['precheckout_approved_at'] and row['expires_at']
                and row['expires_at']<=datetime.now(timezone.utc)):
                row=conn.execute("UPDATE wallet_topups SET status='failed',invoice_url=NULL WHERE id=%s RETURNING *",(tid,)).fetchone()
            return view(row)
        if row['status'] in ('failed','refunded','review'):return view(row)
        if not row['provider_payment_id']:raise HTTPException(409,'wallet_resume_topup')
        if row['provider'] in acquirers.MINIMUM:
            verify_acquirer(conn, row)
            return view(conn.execute('SELECT * FROM wallet_topups WHERE id=%s',(tid,)).fetchone())
        cfg=sbp_settings()
        if not cfg['configured'] or row['provider_account']!=cfg['shop'] or row['test_mode']!=cfg['test_mode']:
            raise HTTPException(409,'sbp_configuration_changed')
        payment=sbp_provider.get_sbp_payment(row['provider_payment_id'])
        settle_sbp(conn,row,payment)
        return view(conn.execute('SELECT * FROM wallet_topups WHERE id=%s',(tid,)).fetchone())


def stars_identity(row,payload):
    _,bot_id=stars_checkout.credentials()
    return bool(row and row['provider']=='telegram_stars' and row['provider_account']==str(bot_id)
        and row['test_mode']==is_test_environment() and row['telegram_user_id']==payload.telegram_user_id
        and payload.currency=='XTR' and row['amount']==payload.total_amount)


def locked_star(conn,tid):
    return conn.execute('''SELECT t.*,u.telegram_user_id,u.blocked_at FROM wallet_topups t JOIN users u ON u.id=t.user_id
        WHERE t.id=%s FOR UPDATE OF t''',(tid,)).fetchone() if tid else None


def precheckout(dsn,payload):
    denied={'ok':False,'error_message':'Счёт устарел или изменился. Откройте «Баланс» и проверьте пополнение.'}
    if not stars_invoices_enabled():return denied
    with connect(dsn) as conn:
        row=locked_star(conn,parse_payload(payload.invoice_payload))
        if (not stars_identity(row,payload) or row.get('blocked_at') is not None or row['status']!='pending' or not row['expires_at']
            or row['expires_at']<=datetime.now(timezone.utc) or row['precheckout_query_id'] not in (None,payload.query_id)):
            return denied
        conn.execute('UPDATE wallet_topups SET precheckout_query_id=%s,precheckout_approved_at=coalesce(precheckout_approved_at,now()) WHERE id=%s',(payload.query_id,row['id']))
    return {'ok':True}


def apply_star_refund(conn,row,charge):
    event=conn.execute('SELECT * FROM wallet_star_refunds WHERE charge_id=%s FOR UPDATE',(charge,)).fetchone()
    if not event or event['status']=='applied':return
    if (event['telegram_user_id']!=row['telegram_user_id'] or event['currency']!='XTR' or event['amount']!=row['amount']
        or event['provider_account']!=row['provider_account'] or event['test_mode']!=row['test_mode']):
        conn.execute("UPDATE wallet_star_refunds SET status='review' WHERE charge_id=%s",(charge,));return
    if row['status'] not in ('succeeded','refunded'):return
    if not row['test_mode']:
        ledger.post(conn,row['user_id'],'XTR',-row['amount'],'provider_refund','provider-refund:'+str(row['id']),
                    'Возврат Stars платёжной системой',topup=row['id'])
    conn.execute("UPDATE wallet_topups SET status='refunded',refunded_amount=amount WHERE id=%s",(row['id'],))
    conn.execute("UPDATE wallet_star_refunds SET status='applied' WHERE charge_id=%s",(charge,))


def settle_stars(dsn,payload):
    with connect(dsn) as conn:
        conn.execute('''INSERT INTO stars_payment_receipts(telegram_payment_charge_id,payer_telegram_user_id,currency,total_amount)
            VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (payload.telegram_payment_charge_id,payload.telegram_user_id,payload.currency,payload.total_amount))
        receipt=conn.execute('SELECT * FROM stars_payment_receipts WHERE telegram_payment_charge_id=%s FOR UPDATE',(payload.telegram_payment_charge_id,)).fetchone()
        tid=parse_payload(payload.invoice_payload)
        def unresolved(reason):
            if receipt['handling_status']!='applied':
                conn.execute("UPDATE stars_payment_receipts SET handling_status='requires_review',reason_code=%s,processed_at=now() WHERE id=%s",(reason,receipt['id']))
            return {'status':'requires_review'}
        if (receipt['payer_telegram_user_id']!=payload.telegram_user_id or receipt['currency'].strip()!=payload.currency
            or receipt['total_amount']!=payload.total_amount):return unresolved('wallet_receipt_conflict')
        if receipt['handling_status']=='applied':
            return {'status':'already_processed'} if receipt['wallet_topup_id']==tid and tid else unresolved('wallet_receipt_reused')
        row=locked_star(conn,tid)
        if not stars_identity(row,payload):return unresolved('wallet_identity_mismatch')
        if row['status']!='pending' or not row['precheckout_approved_at'] or row['provider_payment_id']:
            return unresolved('wallet_not_pending_or_precheckout_missing')
        # Один charge не может оплатить и кампанию, и баланс.
        if conn.execute("SELECT 1 FROM payment_attempts WHERE provider='telegram_stars' AND provider_payment_id=%s",(payload.telegram_payment_charge_id,)).fetchone():
            return unresolved('wallet_charge_reused')
        if not row['test_mode']:
            ledger.post(conn,row['user_id'],'XTR',row['amount'],'topup','topup:'+str(tid),'Пополнение через Stars',topup=tid)
        conn.execute("UPDATE wallet_topups SET status='succeeded',provider_payment_id=%s,settled_at=now(),invoice_url=NULL WHERE id=%s",(payload.telegram_payment_charge_id,tid))
        conn.execute("UPDATE stars_payment_receipts SET handling_status='applied',wallet_topup_id=%s,processed_at=now(),reason_code=NULL WHERE id=%s",(tid,receipt['id']))
        row['status']='succeeded';apply_star_refund(conn,row,payload.telegram_payment_charge_id)
        return {'status':'applied','test_mode':row['test_mode']}


def refund_stars(dsn,payload):
    _,bot_id=stars_checkout.credentials()
    with connect(dsn) as conn:
        # Совпадает с порядком settle: receipt → topup → refund event → accounts.
        receipt=conn.execute('SELECT * FROM stars_payment_receipts WHERE telegram_payment_charge_id=%s FOR UPDATE',(payload.telegram_payment_charge_id,)).fetchone()
        row=locked_star(conn,receipt['wallet_topup_id']) if receipt and receipt['wallet_topup_id'] else None
        conn.execute('''INSERT INTO wallet_star_refunds(charge_id,telegram_user_id,currency,amount,provider_account,test_mode)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (payload.telegram_payment_charge_id,payload.telegram_user_id,payload.currency,payload.total_amount,str(bot_id),is_test_environment()))
        if row:apply_star_refund(conn,row,payload.telegram_payment_charge_id)
        return {'status':'recorded'}
