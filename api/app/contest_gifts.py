"""Competition prize allocation, Telegram sharing and single-recipient redemption."""
import hashlib
import json
import os
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from . import service_messages, vpn_shop


class EventText(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=1, max_length=90)
    sender: str = Field(min_length=1, max_length=64)
    greeting: str = Field(default='', max_length=400)


class Allocation(EventText):
    request_id: UUID | None = None
    organizer: int = Field(gt=0, lt=2**52)
    month_limit: int = Field(ge=0, le=10000)
    quarter_limit: int = Field(ge=0, le=10000)


class EventEdit(EventText):
    expected_revision: int | None = Field(default=None, ge=0, strict=True)


class EventManagement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    organizer: int = Field(gt=0, lt=2**52)
    month_limit: int = Field(ge=0, le=10000)
    quarter_limit: int = Field(ge=0, le=10000)
    archived: bool = Field(strict=True)
    expected_revision: int | None = Field(default=None, ge=0, strict=True)


def can_manage_events(user):
    return bool(user.get('is_tamada') or user.get('is_owner') or
                user.get('is_developer') or user.get('role') in ('admin', 'developer'))


class Prize(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    recipient: int | None = Field(default=None, gt=0, lt=2**52)
    days: Literal[30, 90]


def router_for(dsn, current_user, require_admin):
    def private(response: Response):
        response.headers['Cache-Control'] = 'private, no-store'

    router = APIRouter(prefix='/api/v1/contests', dependencies=[Depends(private)])

    def db():
        return psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=3)

    def actor(conn, user):
        # Lock current authority before the event/prize; concurrent status removal
        # and account blocking cannot race a write using a stale session snapshot.
        row = conn.execute("""SELECT id,telegram_user_id,role,is_owner,is_developer,is_tamada,blocked_at
            FROM users WHERE id=%s FOR SHARE""", (user['id'],)).fetchone()
        if not row or row['blocked_at'] or row['telegram_user_id'] != user['telegram_user_id']:
            raise HTTPException(401, 'user_blocked')
        # Match the session used by get_current_user, not just its user id.
        # Session revocation/expiry after authentication must stop stale requests.
        session_id = user.get('session_id')
        if session_id is not None and not conn.execute("""SELECT id FROM user_sessions
            WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>clock_timestamp()
            FOR SHARE""", (session_id,row['id'])).fetchone():
            raise HTTPException(401, 'session_revoked_or_expired')
        return row

    def require_event_manager(conn, user):
        user = actor(conn, user)
        if not can_manage_events(user):
            raise HTTPException(403, 'Недостаточно прав для управления мероприятиями')
        return user

    def owned(conn, event_id, user, write=True):
        event = conn.execute('SELECT * FROM contest_events WHERE id=%s' +
                             (' FOR UPDATE' if write else ' FOR SHARE'), (event_id,)).fetchone()
        if not event or (not can_manage_events(user) and event['organizer'] != user['telegram_user_id']):
            raise HTTPException(404, 'Мероприятие не найдено')
        return event

    def active(event):
        if event['archived_at'] is not None:
            raise HTTPException(409, 'Мероприятие в архиве')

    def check_revision(event, expected):
        if expected is not None and expected != event['revision']:
            raise HTTPException(409, 'Мероприятие изменилось. Обновите страницу и повторите действие')

    def audit(conn, user, event_id, kind, details):
        conn.execute("""INSERT INTO audit_events(actor_user_id,event_type,details_json)
            VALUES(%s,%s,%s::jsonb)""",
            (user['id'], kind, json.dumps({'event_id': str(event_id), **details})))

    def visible(conn, prize_id, user, lock=False):
        query = """SELECT p.*,e.title,e.sender,e.greeting FROM contest_prizes p
                   JOIN contest_events e ON e.id=p.event_id WHERE p.id=%s"""
        row = conn.execute(query + (' FOR UPDATE OF p' if lock else ''), (prize_id,)).fetchone()
        if not row or row['recipient'] not in (None, user['telegram_user_id']):
            raise HTTPException(404, 'Подарок не найден для этого аккаунта')
        if row['revoked'] or row['winner'] not in (None, user['id']):
            raise HTTPException(410, 'Подарок уже получен или отозван')
        return row

    @router.get('')
    def events(user=Depends(current_user)):
        with db() as conn:
            user = actor(conn, user)
            return conn.execute("""SELECT id,organizer,title,sender,greeting,month_limit,quarter_limit,
                created,archived_at,revision FROM contest_events WHERE organizer=%s OR %s
                ORDER BY archived_at NULLS FIRST, created DESC""", (user['telegram_user_id'], can_manage_events(user))).fetchall()

    @router.post('')
    def allocate(data: Allocation, user=Depends(current_user)):
        values = data.model_dump(exclude={'request_id'})
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with db() as conn:
            user = require_event_manager(conn, user)
            values.update(creation_actor=user['id'], creation_request_id=data.request_id, creation_fingerprint=fingerprint)
            result = conn.execute("""INSERT INTO contest_events(title,sender,greeting,organizer,month_limit,quarter_limit,
                creation_actor,creation_request_id,creation_fingerprint)
                VALUES (%(title)s,%(sender)s,%(greeting)s,%(organizer)s,%(month_limit)s,%(quarter_limit)s,
                    %(creation_actor)s,%(creation_request_id)s,%(creation_fingerprint)s)
                ON CONFLICT(creation_actor,creation_request_id) DO NOTHING RETURNING id""", values).fetchone()
            if result:
                audit(conn, user, result['id'], 'contest_created',
                      {key:values[key] for key in ('organizer','month_limit','quarter_limit')})
                return result
            existing = conn.execute("""SELECT id,creation_fingerprint FROM contest_events
                WHERE creation_actor=%s AND creation_request_id=%s""",(user['id'],data.request_id)).fetchone()
            if not existing or existing['creation_fingerprint'] != fingerprint:
                raise HTTPException(409, 'Запрос создания уже использован')
            return {'id':existing['id']}

    @router.get('/mine')
    def mine(user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            return conn.execute("""SELECT p.id,p.days,p.expires,e.title,e.sender FROM contest_prizes p
                JOIN contest_events e ON e.id=p.event_id WHERE winner=%s ORDER BY p.created DESC""",(user['id'],)).fetchall()

    @router.get('/events/{event_id}')
    def details(event_id: UUID, user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            event = owned(conn,event_id,user,write=False)
            prizes = conn.execute("""SELECT p.*,u.telegram_user_id AS winner_telegram_id
                FROM contest_prizes p LEFT JOIN users u ON u.id=p.winner
                WHERE p.event_id=%s ORDER BY p.created DESC""",(event_id,)).fetchall()
            public = {key:value for key,value in event.items() if not key.startswith('creation_')}
            return dict(public, prizes=prizes)

    @router.put('/events/{event_id}')
    def edit(event_id: UUID, data: EventEdit, user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            event = owned(conn,event_id,user)
            active(event)
            check_revision(event,data.expected_revision)
            conn.execute('UPDATE contest_events SET title=%s,sender=%s,greeting=%s,revision=revision+1 WHERE id=%s',
                         (data.title,data.sender,data.greeting,event_id))
            audit(conn,user,event_id,'contest_card_edited',{})
        return {'ok':True}

    @router.put('/events/{event_id}/management')
    def manage_event(event_id: UUID, data: EventManagement, user=Depends(current_user)):
        with db() as conn:
            user = require_event_manager(conn,user)
            event = owned(conn, event_id, user)
            check_revision(event,data.expected_revision)
            used = {row['days']: row['n'] for row in conn.execute(
                """SELECT days,count(*) AS n FROM contest_prizes
                   WHERE event_id=%s AND NOT revoked GROUP BY days""", (event_id,)).fetchall()}
            if data.month_limit < used.get(30, 0) or data.quarter_limit < used.get(90, 0):
                raise HTTPException(409, 'Лимит не может быть меньше числа выданных подарков')
            conn.execute("""UPDATE contest_events SET organizer=%s,month_limit=%s,quarter_limit=%s,
                archived_at=CASE WHEN %s THEN coalesce(archived_at,now()) ELSE NULL END,
                revision=revision+1 WHERE id=%s""",
                (data.organizer,data.month_limit,data.quarter_limit,data.archived,event_id))
            audit(conn,user,event_id,'contest_management_changed',
                  data.model_dump(exclude={'expected_revision'}))
        return {'ok':True}

    @router.post('/events/{event_id}/prizes')
    def issue(event_id: UUID, data: Prize, user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            event = owned(conn,event_id,user)
            old = conn.execute('SELECT * FROM contest_prizes WHERE event_id=%s AND request_id=%s',
                               (event_id,data.request_id)).fetchone()
            if old:
                if (old['days'],old['recipient']) != (data.days,data.recipient):
                    raise HTTPException(409,'Запрос уже использован')
                return old
            active(event)
            used = conn.execute('SELECT count(*) AS n FROM contest_prizes WHERE event_id=%s AND days=%s AND NOT revoked',
                                (event_id,data.days)).fetchone()['n']
            if used >= event['month_limit' if data.days==30 else 'quarter_limit']:
                raise HTTPException(409,'Призы на этот срок закончились')
            result = conn.execute('''INSERT INTO contest_prizes(event_id,request_id,recipient,days)
                VALUES (%s,%s,%s,%s) RETURNING *''',(event_id,data.request_id,data.recipient,data.days)).fetchone()
            audit(conn,user,event_id,'contest_prize_issued',{'days':data.days,'personal':data.recipient is not None})
            return result

    @router.post('/events/{event_id}/prizes/{prize_id}/{action}')
    def manage(event_id: UUID, prize_id: UUID, action: Literal['share','revoke'], user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            event = owned(conn,event_id,user)
            row = conn.execute('SELECT * FROM contest_prizes WHERE id=%s AND event_id=%s FOR UPDATE',
                               (prize_id,event_id)).fetchone()
            if not row or row['revoked'] or row['winner']:
                raise HTTPException(409,'Подарок недоступен')
            if action=='share':
                active(event)
            if action=='revoke':
                conn.execute('UPDATE contest_prizes SET revoked=true WHERE id=%s',(prize_id,))
                audit(conn,user,event_id,'contest_prize_revoked',{'days':row['days']})
                return {'ok':True}
        url = f'https://t.me/{service_messages.CABINET_BOT}?startapp=prize_{prize_id.hex}'
        image = f'https://pushok.shop/assets/flow-gift-{row["days"]}.jpg'
        caption = '\n\n'.join(filter(None,[f'🎁 Pushok Flow на {row["days"]} дней',event['title'],event['greeting'],'От '+event['sender']]))
        token = os.getenv('TELEGRAM_BOT_TOKEN','')
        if not token:
            raise HTTPException(503,'Отправка карточки временно недоступна')
        service_messages.verify_sender(token)
        prepared = service_messages.telegram_call(token,'savePreparedInlineMessage',{
            'user_id':user['telegram_user_id'],'allow_user_chats':True,'allow_group_chats':True,'allow_channel_chats':True,
            'result':{'type':'photo','id':prize_id.hex,'photo_url':image,'thumbnail_url':image,'caption':caption,
                      'reply_markup':{'inline_keyboard':[[{'text':'Забрать подарок','url':url}]]}}})
        prepared_id = prepared.get('id') if isinstance(prepared,dict) else None
        if not isinstance(prepared_id,str) or not prepared_id:
            raise HTTPException(503,'Карточка не подготовлена. Скопируйте ссылку или повторите отправку')
        return {'id':prepared_id}

    @router.get('/prizes/{prize_id}')
    def preview(prize_id: UUID, user=Depends(current_user)):
        with db() as conn:
            user = actor(conn,user)
            row = visible(conn,prize_id,user)
            return {**{key:row[key] for key in ('title','sender','greeting','days','expires')},
                    'claim_pending':row['winner'] is not None and row['expires'] is None}

    @router.post('/prizes/{prize_id}/claim')
    def claim(prize_id: UUID, user=Depends(current_user)):
        from .telegram_environment import is_test_environment
        if is_test_environment():
            raise HTTPException(409,'vpn_test_store')
        with db() as conn:
            user = actor(conn,user)
            row = visible(conn,prize_id,user,lock=True)
            if row['expires']:
                return {'expires':row['expires']}
            conn.execute('UPDATE contest_prizes SET winner=%s WHERE id=%s',(user['id'],prize_id))
        # Keep the winner even if Shop commits and its HTTP response gets lost.
        reply = vpn_shop.exchange('gift',user,gift_id=str(prize_id),days=row['days'])
        try:
            result = json.loads(reply.body)
            expires = result['expires']
            if expires is not None and (type(expires) is not int or not 0 < expires < 2**63):
                raise ValueError('invalid gift expiry')
        except (ValueError,TypeError,KeyError):
            raise HTTPException(503,'vpn_shop_unavailable') from None
        # Never replace a previously confirmed expiration with a late response.
        with db() as conn:
            saved = conn.execute("""UPDATE contest_prizes SET expires=coalesce(expires,%s)
                WHERE id=%s AND winner=%s RETURNING expires""",(expires,prize_id,user['id'])).fetchone()
        if not saved:
            raise HTTPException(409,'Подарок уже недоступен')
        return {'expires':saved['expires']}

    return router
