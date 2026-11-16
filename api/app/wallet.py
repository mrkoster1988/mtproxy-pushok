"""Внутренний баланс: API кабинета, администратор и закрытые callbacks бота."""
import csv
import io
from datetime import timezone
from contextlib import contextmanager
from typing import Literal
from uuid import UUID
import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Response, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from . import wallet_ledger as ledger, wallet_topups as topups, wallet_controls as controls, wallet_history
from .bot_stars import StarsPrecheckoutInput, StarsReceiptInput, authorize
from .sbp_provider import SBPProviderError
from .sbp_verification import SBPVerificationError
from .stars_checkout import StarsLinkError


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Topup(Input):
    request_id: UUID
    currency: Literal['RUB','XTR']
    amount: int=Field(gt=0,le=100000000,strict=True)


class WalletSettings(Input):
    enabled: bool=Field(strict=True)


class Preferences(Input):
    auto_pay: bool
    currency: Literal['RUB','XTR']
    per_campaign_limit: int=Field(ge=0,le=100000000,strict=True)
    use_bonus: bool=True

    @model_validator(mode='after')
    def limit_required(self):
        if self.auto_pay and self.per_campaign_limit<=0:raise ValueError('Укажите предел автосписания')
        return self


class Pay(Input):
    currency: Literal['RUB','XTR']
    use_bonus: bool=True
    expected_amount: int=Field(gt=0,le=100000000,strict=True)


class Reason(Input):
    reason: str=Field(min_length=3,max_length=500)

    @field_validator('reason')
    @classmethod
    def nonempty(cls,value):
        value=value.strip()
        if len(value)<3:raise ValueError('Укажите причину операции')
        return value


class Adjustment(Reason):
    request_id: UUID
    currency: Literal['RUB','XTR','BONUS']
    amount: int=Field(gt=0,le=100000000,strict=True)
    direction: Literal['credit','debit']


class StarRefund(Input):
    telegram_user_id: int=Field(gt=0,strict=True)
    currency: Literal['XTR']
    total_amount: int=Field(gt=0,le=100000000,strict=True)
    telegram_payment_charge_id: str=Field(min_length=1,max_length=255)


def csv_cell(value):
    text='' if value is None else str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else text


def router_for(dsn,current_user,require_admin):
    from . import wallet_gifts as gifts
    router=APIRouter()
    @contextmanager
    def db():
        try:
            with topups.connect(dsn()) as conn:yield conn
        except psycopg.Error as exc:raise HTTPException(503,'database_unavailable') from exc

    def provider_call(fn,*args):
        try:return fn(dsn(),*args)
        except psycopg.Error as exc:raise HTTPException(503,'database_unavailable') from exc
        except (SBPProviderError,StarsLinkError) as exc:raise HTTPException(503,'wallet_provider_unavailable') from exc
        except SBPVerificationError as exc:raise HTTPException(409,'wallet_provider_mismatch') from exc

    def admin_lock(conn,admin):
        row=conn.execute('SELECT role FROM users WHERE id=%s FOR SHARE',(admin['id'],)).fetchone()
        if not row or row['role']!='admin':raise HTTPException(403,'admin_role_required')

    def target(conn,tg):
        user=conn.execute('SELECT id,telegram_user_id,first_name,last_name,username FROM users WHERE telegram_user_id=%s',(tg,)).fetchone()
        if not user:raise HTTPException(404,'wallet_user_not_found')
        return user

    def summary(conn,uid):
        balances=ledger.accounts(conn,uid)
        prefs=conn.execute('SELECT auto_pay,currency,per_campaign_limit,use_bonus FROM wallet_preferences WHERE user_id=%s',(uid,)).fetchone()
        return {'enabled':controls.settings(conn)['enabled'],'balances':balances,'has_debt':any(v<0 for v in balances.values()),'preferences':prefs or
            dict(auto_pay=False,currency='RUB',per_campaign_limit=0,use_bonus=True)}

    def journal(conn,uid,page,limit=30):
        rows=conn.execute('''SELECT e.id,e.entry_number,e.currency,e.delta,e.balance_after,e.kind,e.reason,e.created_at,e.campaign_id,
            e.topup_id,e.reversal_of,EXISTS(SELECT 1 FROM wallet_payments p WHERE p.campaign_id=e.campaign_id AND p.automatic) AS automatic,u.username AS actor_username,u.telegram_user_id AS actor_telegram_id,
            EXISTS(SELECT 1 FROM wallet_entries r WHERE r.reversal_of=e.id) AS reversed
            FROM wallet_entries e LEFT JOIN users u ON u.id=e.actor_user_id WHERE e.user_id=%s
            ORDER BY e.entry_number DESC LIMIT %s OFFSET %s''',(uid,limit,page*limit)).fetchall()
        count=conn.execute('SELECT count(*) AS n FROM wallet_entries WHERE user_id=%s',(uid,)).fetchone()['n']
        return {'entries':rows,'page':page,'total':count,'page_size':limit}

    @router.get('/api/v1/wallet')
    def own(response:Response,deleted_topups:bool=False,user=Depends(current_user)):
        response.headers['Cache-Control']='private, no-store'
        with db() as conn:
            result=summary(conn,user['id'])
            result['topups']=[dict(topups.view(r),hidden_at=r['hidden_at']) for r in conn.execute('SELECT * FROM wallet_topups WHERE user_id=%s AND (hidden_at IS NOT NULL)=%s ORDER BY created_at DESC,id DESC LIMIT 30',(user['id'],deleted_topups)).fetchall()]
        try:topups.stars_checkout.credentials();stars_configured=True
        except HTTPException:stars_configured=False
        cfg=topups.sbp_settings()
        result.update(stars_only=user.get('login_surface')=='telegram',
            stars_available=stars_configured and topups.stars_invoices_enabled(),
            sbp_available=cfg['enabled'] and user.get('login_surface')!='telegram')
        return result

    @router.get('/api/v1/admin/wallet/settings')
    def wallet_settings(admin=Depends(require_admin)):
        with db() as conn:return controls.settings(conn)

    @router.put('/api/v1/admin/wallet/settings')
    def set_wallet_settings(data:WalletSettings,admin=Depends(require_admin)):
        with db() as conn:
            conn.execute('SELECT singleton FROM wallet_settings WHERE singleton FOR UPDATE')
            admin_lock(conn,admin)
            conn.execute('UPDATE wallet_settings SET enabled=%s,updated_at=now(),updated_by=%s WHERE singleton',(data.enabled,admin['id']))
            if not data.enabled:conn.execute('UPDATE wallet_preferences SET auto_pay=false WHERE auto_pay')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_enabled_changed',jsonb_build_object('enabled',%s::boolean))",(admin['id'],data.enabled))
            return controls.settings(conn)

    @router.post('/api/v1/wallet/gifts/redeem')
    def redeem_gift(data:gifts.GiftRedeem,user=Depends(current_user)):
        with db() as conn:return gifts.redeem(conn,user['id'],data.code)

    @router.get('/api/v1/admin/wallet/gifts')
    def list_gifts(page:int=Query(default=0,ge=0,le=100000),deleted:bool=False,admin=Depends(require_admin)):
        with db() as conn:
            rows=conn.execute("""SELECT g.*, (SELECT count(*) FROM wallet_gift_redemptions r WHERE r.gift_id=g.id) AS used,
                g.expires_at<=now() AS expired,u.username AS recipient_username FROM wallet_gifts g
                LEFT JOIN users u ON u.telegram_user_id=g.recipient_telegram_id WHERE (g.deleted_at IS NOT NULL)=%s
                ORDER BY g.created_at DESC,g.id LIMIT 30 OFFSET %s""",(deleted,page*30)).fetchall()
            total=conn.execute('SELECT count(*) AS n FROM wallet_gifts WHERE (deleted_at IS NOT NULL)=%s',(deleted,)).fetchone()['n']
            return dict(items=rows,total=total,page=page,page_size=30)

    @router.get('/api/v1/admin/wallet/gifts/{gid}/redemptions')
    def gift_report(gid:UUID,response:Response,page:int=Query(default=0,ge=0,le=100000),admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        with db() as conn:return gifts.report(conn,gid,page=page)

    @router.get('/api/v1/admin/wallet/gifts/{gid}/export')
    def gift_export(gid:UUID,admin=Depends(require_admin)):
        with db() as conn:data=gifts.report(conn,gid,limit=10000)
        output=io.StringIO();writer=csv.writer(output,delimiter=';')
        writer.writerow(['Подарок','Проводка','ID проводки','Дата UTC','Telegram ID','Username','Имя',
                         'Начислено (бонусные копейки)','Остаток после операции (бонусные копейки)'])
        for e in data['items']:
            writer.writerow([csv_cell(data['gift']['name']),e['entry_number'],e['entry_id'],e['created_at'].astimezone(timezone.utc).isoformat(),
                e['telegram_user_id'],csv_cell(e['username']),csv_cell(e['first_name']),e['amount'],e['balance_after']])
        return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':f'attachment; filename="gift-{gid}.csv"','Cache-Control':'private, no-store'})

    @router.post('/api/v1/admin/wallet/gifts',status_code=201)
    def create_gift(data:gifts.GiftCreate,admin=Depends(require_admin)):
        with db() as conn:
            return gifts.create(conn,admin['id'],data)

    @router.put('/api/v1/admin/wallet/gifts/{gid}')
    def set_gift(gid:UUID,data:gifts.GiftActive,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            row=conn.execute('UPDATE wallet_gifts SET active=%s WHERE id=%s AND deleted_at IS NULL RETURNING id,active',(data.active,gid)).fetchone()
            if not row:raise HTTPException(404,'wallet_gift_unavailable')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_gift_enabled_changed',jsonb_build_object('gift_id',%s::text,'active',%s::boolean))",(admin['id'],str(gid),data.active))
            return row

    @router.patch('/api/v1/admin/wallet/gifts/{gid}')
    def edit_gift(gid:UUID,data:gifts.GiftDetails,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            return gifts.edit(conn,admin['id'],gid,data)

    @router.delete('/api/v1/admin/wallet/gifts/{gid}')
    def delete_gift(gid:UUID,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            row=conn.execute('UPDATE wallet_gifts SET active=false,deleted_at=coalesce(deleted_at,now()) WHERE id=%s RETURNING id',(gid,)).fetchone()
            if not row:raise HTTPException(404,'wallet_gift_unavailable')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_gift_deleted',jsonb_build_object('gift_id',%s::uuid))",(admin['id'],gid))
            return {'saved':True}

    @router.post('/api/v1/admin/wallet/gifts/{gid}/restore')
    def restore_gift(gid:UUID,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            row=conn.execute('UPDATE wallet_gifts SET active=false,deleted_at=NULL WHERE id=%s AND deleted_at IS NOT NULL RETURNING id',(gid,)).fetchone()
            if not row:raise HTTPException(404,'wallet_gift_unavailable')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_gift_restored',jsonb_build_object('gift_id',%s::uuid))",(admin['id'],gid))
            return {'saved':True}

    @router.get('/api/v1/wallet/entries')
    def entries(page:int=Query(default=0,ge=0,le=100000),user=Depends(current_user)):
        with db() as conn:return wallet_history.history(conn,user['id'],page)

    @router.delete('/api/v1/wallet/topups/{tid}')
    def hide_topup(tid:UUID,user=Depends(current_user)):
        with db() as conn:
            row=conn.execute('SELECT status,hidden_at FROM wallet_topups WHERE id=%s AND user_id=%s FOR UPDATE',(tid,user['id'])).fetchone()
            if not row:raise HTTPException(404,'wallet_topup_not_found')
            if row['status'] not in ('failed','succeeded','refunded'):
                raise HTTPException(409,'wallet_topup_not_closed')
            if row['hidden_at'] is None:
                conn.execute('UPDATE wallet_topups SET hidden_at=now() WHERE id=%s',(tid,))
                conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_invoice_hidden',jsonb_build_object('topup_id',%s::uuid))",(user['id'],tid))
            return {'saved':True}

    @router.post('/api/v1/wallet/topups/{tid}/restore')
    def restore_topup(tid:UUID,user=Depends(current_user)):
        with db() as conn:
            row=conn.execute('UPDATE wallet_topups SET hidden_at=NULL WHERE id=%s AND user_id=%s RETURNING id',(tid,user['id'])).fetchone()
            if not row:raise HTTPException(404,'wallet_topup_not_found')
            return {'saved':True}

    @router.put('/api/v1/wallet/preferences')
    def preferences(data:Preferences,user=Depends(current_user)):
        if data.auto_pay and data.currency=='XTR':
            raise HTTPException(409,'wallet_stars_topup_retired')
        if data.auto_pay and data.currency=='RUB' and user.get('login_surface')=='telegram':
            raise HTTPException(409,'wallet_stars_only_in_telegram')
        with db() as conn:
            if data.auto_pay:controls.require_enabled(conn)
            conn.execute('''INSERT INTO wallet_preferences(user_id,auto_pay,currency,per_campaign_limit,use_bonus)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET auto_pay=EXCLUDED.auto_pay,currency=EXCLUDED.currency,
                per_campaign_limit=EXCLUDED.per_campaign_limit,use_bonus=EXCLUDED.use_bonus''',
                (user['id'],data.auto_pay,data.currency,data.per_campaign_limit,data.use_bonus))
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_autopay_changed',jsonb_build_object('enabled',%s::boolean,'currency',%s::text,'limit',%s::bigint))",(user['id'],data.auto_pay,data.currency,data.per_campaign_limit))
        return data

    @router.get('/api/v1/wallet/campaigns/{cid}')
    def quote(cid:UUID,user=Depends(current_user)):
        with db() as conn:
            c=ledger.campaign(conn,cid,user['id'])
            paid=conn.execute('SELECT * FROM wallet_payments WHERE campaign_id=%s',(cid,)).fetchone()
            return dict(campaign_id=cid,amount_rub=c['price_kopecks_snapshot'],amount_xtr=c['wallet_xtr'],
                stars_only=user.get('login_surface')=='telegram' or c['purchase_surface']=='telegram',
                payable=controls.settings(conn)['enabled'] and ledger.payable(conn,c),payment=paid,**summary(conn,user['id']))

    @router.post('/api/v1/wallet/campaigns/{cid}/pay')
    def pay(cid:UUID,data:Pay,user=Depends(current_user)):
        with db() as conn:return ledger.charge(conn,cid,user['id'],data.currency,use_bonus=data.use_bonus,
            expected=data.expected_amount,surface=user.get('login_surface','web'))

    @router.post('/api/v1/wallet/topups',status_code=201)
    def create(data:Topup,user=Depends(current_user)):
        return provider_call(topups.create,user,data)

    @router.post('/api/v1/wallet/topups/{tid}/resume')
    def resume(tid:UUID,user=Depends(current_user)):
        return provider_call(topups.resume,tid,user)

    @router.post('/api/v1/wallet/topups/{tid}/verify')
    def verify(tid:UUID,user=Depends(current_user)):
        return provider_call(topups.verify,tid,user['id'])

    @router.get('/api/v1/admin/wallet/users')
    def search_users(query:str=Query(default='',max_length=100),admin=Depends(require_admin)):
        value=query.strip().lstrip('@')
        with db() as conn:
            return conn.execute("""SELECT id,telegram_user_id,first_name,last_name,username FROM users
                WHERE telegram_user_id>0 AND (%s='' OR telegram_user_id::text=%s
                  OR position(lower(%s) in lower(coalesce(username,'')))>0
                  OR position(lower(%s) in lower(concat_ws(' ',first_name,last_name)))>0)
                ORDER BY created_at DESC,id LIMIT 20""",(value,value,value,value)).fetchall()

    @router.get('/api/v1/admin/wallet/users/{tg}')
    def admin_user(tg:int,page:int=Query(default=0,ge=0,le=100000),admin=Depends(require_admin)):
        with db() as conn:
            user=target(conn,tg);uid=user['id']
            # Остаток и суммы журнала читаются, пока проводки этого пользователя заблокированы.
            ledger.accounts(conn,uid,lock=True)
            totals=conn.execute('''SELECT currency,coalesce(sum(delta) FILTER(WHERE delta>0),0)::bigint AS credited,
                coalesce(-sum(delta) FILTER(WHERE delta<0),0)::bigint AS debited,coalesce(sum(delta),0)::bigint AS journal_balance
                FROM wallet_entries WHERE user_id=%s GROUP BY currency''',(uid,)).fetchall()
            payments=conn.execute('SELECT * FROM wallet_payments WHERE user_id=%s ORDER BY created_at DESC LIMIT 30',(uid,)).fetchall()
            data=summary(conn,uid)
            data['reconciled']=all(data['balances'][c]==next((r['journal_balance'] for r in totals if r['currency']==c),0) for c in ledger.CURRENCIES)
            return dict(user=user,totals=totals,payments=payments,**data,**journal(conn,uid,page))

    @router.post('/api/v1/admin/wallet/users/{tg}/adjust',status_code=201)
    def adjust(tg:int,data:Adjustment,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin);uid=target(conn,tg)['id']
            key='manual:'+str(admin['id'])+':'+str(data.request_id)
            if data.currency=='XTR':
                balance=ledger.accounts(conn,uid,lock=True)['XTR']
                existing=conn.execute('SELECT 1 FROM wallet_entries WHERE operation_key=%s',(key,)).fetchone()
                if not existing and (data.direction!='credit' or balance>=0 or data.amount>-balance):
                    raise HTTPException(409,'wallet_stars_topup_retired')
            return ledger.post(conn,uid,data.currency,data.amount*(1 if data.direction=='credit' else -1),
                'manual_'+data.direction,key,data.reason,actor=admin['id'])

    @router.post('/api/v1/admin/wallet/entries/{eid}/reverse')
    def reverse(eid:UUID,data:Reason,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            entry=conn.execute('SELECT * FROM wallet_entries WHERE id=%s',(eid,)).fetchone()
            if not entry:raise HTTPException(404,'wallet_entry_not_found')
            if entry['kind'] not in ('manual_credit','manual_debit'):raise HTTPException(409,'wallet_use_payment_refund')
            return ledger.post(conn,entry['user_id'],entry['currency'],-entry['delta'],'reversal','reversal:'+str(eid),
                data.reason,actor=admin['id'],reversal=eid)

    @router.post('/api/v1/admin/wallet/campaigns/{cid}/refund')
    def refund(cid:UUID,data:Reason,admin=Depends(require_admin)):
        with db() as conn:
            admin_lock(conn,admin)
            return ledger.refund_campaign(conn,cid,admin['id'],data.reason)

    @router.get('/api/v1/admin/wallet/topups')
    def review(page:int=Query(default=0,ge=0,le=100000),admin=Depends(require_admin)):
        with db() as conn:
            return conn.execute('''SELECT t.id,t.user_id,u.telegram_user_id,u.first_name,u.last_name,u.username,t.currency,t.amount,t.status,t.test_mode,
                t.created_at,t.settled_at,t.review_reason,t.refunded_amount FROM wallet_topups t JOIN users u ON u.id=t.user_id
                ORDER BY t.created_at DESC,t.id DESC LIMIT 30 OFFSET %s''',(page*30,)).fetchall()

    @router.get('/api/v1/admin/wallet/unmatched-stars')
    def unmatched(admin=Depends(require_admin)):
        with db() as conn:
            receipts=conn.execute("""SELECT id,payer_telegram_user_id,currency,total_amount,reason_code,received_at
                FROM stars_payment_receipts WHERE handling_status='requires_review' AND reason_code LIKE 'wallet_%'
                ORDER BY received_at DESC LIMIT 30""").fetchall()
            refunds=conn.execute("""SELECT charge_id,telegram_user_id,currency,amount,status,created_at FROM wallet_star_refunds
                WHERE status<>'applied' ORDER BY created_at DESC LIMIT 30""").fetchall()
            return {'receipts':receipts,'refunds':refunds}

    @router.get('/api/v1/admin/wallet/users/{tg}/export')
    def export(tg:int,admin=Depends(require_admin)):
        with db() as conn:
            user=target(conn,tg);rows=journal(conn,user['id'],0,10000)['entries']
        output=io.StringIO();writer=csv.writer(output,delimiter=';')
        writer.writerow(['Проводка','ID','Дата UTC','Валюта','Изменение (копейки/Stars)','Остаток (копейки/Stars)','Тип','Основание','Автор Telegram ID','Кампания','Пополнение'])
        for e in rows:
            writer.writerow([e['entry_number'],e['id'],e['created_at'].isoformat(),e['currency'],e['delta'],e['balance_after'],e['kind'],csv_cell(e['reason']),e['actor_telegram_id'],e['campaign_id'],e['topup_id']])
        return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="wallet-{tg}.csv"','Cache-Control':'no-store'})

    # Роуты доступны только обработчику update из доверенного Bot API.
    @router.post('/api/v1/internal/bot/wallet/precheckout',include_in_schema=False)
    def pre(data:StarsPrecheckoutInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        return provider_call(topups.precheckout,data)

    @router.post('/api/v1/internal/bot/wallet/settle',include_in_schema=False)
    def settle(data:StarsReceiptInput,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        return provider_call(topups.settle_stars,data)

    @router.post('/api/v1/internal/bot/wallet/refund',include_in_schema=False)
    def refund_event(data:StarRefund,response:Response,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        authorize(x_pushok_bot_secret,response,x_pushok_telegram_environment)
        return provider_call(topups.refund_stars,data)
    return router
