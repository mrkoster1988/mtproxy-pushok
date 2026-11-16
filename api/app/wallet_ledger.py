"""Неизменяемый журнал. Все операции — в транзакции владельца вызывающего кода."""
from datetime import datetime, timezone
from fastapi import HTTPException
from . import wallet_controls as controls

CURRENCIES = ('BONUS', 'RUB', 'XTR')


def accounts(conn, uid, *, lock=False):
    for currency in CURRENCIES:
        conn.execute('INSERT INTO wallet_accounts(user_id,currency) VALUES(%s,%s) ON CONFLICT DO NOTHING', (uid,currency))
    rows=conn.execute('SELECT * FROM wallet_accounts WHERE user_id=%s ORDER BY currency'+(' FOR UPDATE' if lock else ''),(uid,)).fetchall()
    return {r['currency']:r['balance'] for r in rows}


def post(conn, uid, currency, delta, kind, key, reason, *, actor=None, campaign=None, topup=None, reversal=None):
    balances=accounts(conn,uid,lock=True)
    old=conn.execute('SELECT * FROM wallet_entries WHERE operation_key=%s',(key,)).fetchone()
    if old:
        if (old['user_id']!=uid or old['currency']!=currency or old['delta']!=delta or old['kind']!=kind
            or old['reason']!=reason or old['actor_user_id']!=actor or old['campaign_id']!=campaign or old['topup_id']!=topup):
            raise HTTPException(409,'wallet_request_conflict')
        return old
    total=balances[currency]+delta
    if delta<0 and total<0 and kind!='provider_refund':
        raise HTTPException(409,'wallet_insufficient_funds')
    if abs(total)>1000000000000:
        raise HTTPException(409,'wallet_limit_exceeded')
    return conn.execute('''INSERT INTO wallet_entries(user_id,currency,delta,balance_after,kind,operation_key,reason,
        actor_user_id,campaign_id,topup_id,reversal_of) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (uid,currency,delta,total,kind,key,reason,actor,campaign,topup,reversal)).fetchone()


def campaign(conn,cid,uid,*,lock=False):
    c=conn.execute('''SELECT c.*,CASE WHEN c.pricing_version=1 THEN c.stars_price_snapshot ELSE t.stars_price_xtr END AS wallet_xtr
        FROM campaigns c JOIN tariffs t ON t.id=c.tariff_id WHERE c.id=%s AND c.advertiser_user_id=%s'''+(' FOR UPDATE OF c' if lock else ''),(cid,uid)).fetchone()
    if not c:raise HTTPException(404,'campaign_not_found')
    return c


def payable(conn,c):
    if (c['archived_at'] or c['moderation_status']!='approved' or c['payment_status']!='awaiting_payment'
        or c['placement_status']!='not_scheduled' or c['price_kopecks_snapshot']<=0):
        return False
    # Не пересекаемся с выставленным счётом. Истёкший неодобренный Stars можно закрыть.
    conn.execute("UPDATE payment_attempts SET status='failed' WHERE campaign_id=%s AND provider='telegram_stars' AND status='pending' AND expires_at<=now() AND precheckout_approved_at IS NULL",(c['id'],))
    return not conn.execute("SELECT 1 FROM payment_attempts WHERE campaign_id=%s AND status IN ('created','pending','succeeded') LIMIT 1",(c['id'],)).fetchone()


def charge(conn,cid,uid,currency,*,use_bonus=True,expected=None,automatic=False,surface='web'):
    controls.require_enabled(conn)
    c=campaign(conn,cid,uid,lock=True)
    old=conn.execute('SELECT * FROM wallet_payments WHERE campaign_id=%s',(cid,)).fetchone()
    if old:
        if old['status']=='refunded':raise HTTPException(409,'wallet_payment_refunded')
        return old
    if currency=='RUB' and (surface=='telegram' or c['purchase_surface']=='telegram'):
        raise HTTPException(409,'wallet_stars_only_in_telegram')
    if not payable(conn,c):raise HTTPException(409,'wallet_campaign_not_payable')
    amount=c['price_kopecks_snapshot'] if currency=='RUB' else c['wallet_xtr']
    if not amount or amount<=0:raise HTTPException(409,'stars_price_not_configured')
    if expected is not None and amount!=expected:raise HTTPException(409,'wallet_price_changed')
    if automatic:
        prefs=conn.execute('SELECT * FROM wallet_preferences WHERE user_id=%s FOR SHARE',(uid,)).fetchone()
        if not prefs or not prefs['auto_pay'] or prefs['currency']!=currency or amount>prefs['per_campaign_limit']:
            raise HTTPException(409,'wallet_auto_not_authorized')
        use_bonus=prefs['use_bonus']
    balances=accounts(conn,uid,lock=True)
    if any(v<0 for v in balances.values()):raise HTTPException(409,'wallet_provider_refund_debt')
    bonus=min(balances['BONUS'],amount) if use_bonus and currency=='RUB' else 0
    cash=amount-bonus
    if balances[currency]<cash:raise HTTPException(409,'wallet_insufficient_funds')
    pay=conn.execute('''INSERT INTO wallet_payments(campaign_id,user_id,currency,cash_amount,bonus_amount,automatic)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING *''',(cid,uid,currency,cash,bonus,automatic)).fetchone()
    if cash:post(conn,uid,currency,-cash,'payment','payment:'+str(cid)+':'+currency,'Оплата размещения',actor=None if automatic else uid,campaign=cid)
    if bonus:post(conn,uid,'BONUS',-bonus,'payment','payment:'+str(cid)+':BONUS','Оплата размещения бонусами',actor=None if automatic else uid,campaign=cid)
    conn.execute("UPDATE campaigns SET payment_status='verified',updated_at=now() WHERE id=%s",(cid,))
    conn.execute("INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json) VALUES(%s,%s,'wallet_campaign_paid',jsonb_build_object('currency',%s::text,'automatic',%s::boolean))",(uid,cid,currency,automatic))
    return pay


def refund_campaign(conn,cid,admin,reason):
    # Старый маршрут совместим с новым общим журналом, включая частичные возвраты.
    from .refunds import legacy_wallet_refund
    return legacy_wallet_refund(conn,cid,admin,reason)
