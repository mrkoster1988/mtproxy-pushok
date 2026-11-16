"""Подарочные коды начисляют бонусные рубли, не деньги и не Telegram Stars."""
import secrets
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field, field_validator
from .wallet import Input
from . import wallet_ledger as ledger, wallet_controls as controls


class GiftCreate(Input):
    request_id: UUID
    name: str=Field(min_length=3,max_length=100)
    amount: int=Field(ge=1,le=1000000,strict=True)
    max_uses: int=Field(ge=1,le=10000,strict=True)
    expires_at: datetime

    @field_validator('name')
    @classmethod
    def name_clean(cls,value):
        value=value.strip()
        if len(value)<3:raise ValueError('Укажите название подарка')
        return value

    @field_validator('expires_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None:raise ValueError('Нужен часовой пояс')
        return value.astimezone(timezone.utc)


class GiftRedeem(Input):
    code: str=Field(min_length=29,max_length=64)

    @field_validator('code')
    @classmethod
    def clean(cls,value):
        value=value.strip().upper()
        import re
        if not re.fullmatch(r'GIFT-[A-F0-9]{24}',value):raise ValueError('Неверный формат подарочного кода')
        return value


class GiftActive(Input):
    active: bool=Field(strict=True)


def create(conn,admin,data):
    # Один request_id возвращает тот же код даже после неопределённого ответа.
    actor=conn.execute('SELECT role FROM users WHERE id=%s FOR UPDATE',(admin,)).fetchone()
    if not actor or actor['role']!='admin':raise HTTPException(403,'admin_role_required')
    old=conn.execute('SELECT * FROM wallet_gifts WHERE created_by=%s AND request_id=%s',(admin,data.request_id)).fetchone()
    if old:
        if any(old[k]!=getattr(data,k) for k in ('name','amount','max_uses','expires_at')):
            raise HTTPException(409,'wallet_request_conflict')
        return old
    if data.expires_at<=datetime.now(timezone.utc):raise HTTPException(422,'wallet_gift_expired')
    row=conn.execute('''INSERT INTO wallet_gifts(created_by,request_id,code,name,amount,max_uses,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (admin,data.request_id,'GIFT-'+secrets.token_hex(12).upper(),data.name,data.amount,data.max_uses,data.expires_at)).fetchone()
    conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'wallet_gift_created',jsonb_build_object('gift_id',%s::text,'amount',%s::bigint,'max_uses',%s::integer))",(admin,str(row['id']),row['amount'],row['max_uses']))
    return row


def redeem(conn,uid,code):
    controls.require_enabled(conn)
    # Код сериализует весь лимит активаций; проводка и активация коммитятся вместе.
    gift=conn.execute('SELECT * FROM wallet_gifts WHERE code=%s FOR UPDATE',(code,)).fetchone()
    if not gift:raise HTTPException(409,'wallet_gift_unavailable')
    old=conn.execute('SELECT entry_id FROM wallet_gift_redemptions WHERE gift_id=%s AND user_id=%s',(gift['id'],uid)).fetchone()
    if old:return dict(entry_id=old['entry_id'],amount=gift['amount'],already_redeemed=True)
    used=conn.execute('SELECT count(*) AS n FROM wallet_gift_redemptions WHERE gift_id=%s',(gift['id'],)).fetchone()['n']
    now=conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    if not gift['active'] or gift['expires_at']<=now or used>=gift['max_uses']:
        raise HTTPException(409,'wallet_gift_unavailable')
    entry=ledger.post(conn,uid,'BONUS',gift['amount'],'gift_credit',
                      'gift:'+str(gift['id'])+':'+str(uid),'Подарок: '+gift['name'])
    conn.execute('INSERT INTO wallet_gift_redemptions(gift_id,user_id,entry_id) VALUES(%s,%s,%s)',(gift['id'],uid,entry['id']))
    return dict(entry_id=entry['id'],amount=gift['amount'],already_redeemed=False)
