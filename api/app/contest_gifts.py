"""Competition prize allocation, Telegram sharing and single-recipient redemption."""
import json
import os
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from . import service_messages, vpn_shop


class EventText(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=1, max_length=90)
    sender: str = Field(min_length=1, max_length=64)
    greeting: str = Field(default='', max_length=400)


class Allocation(EventText):
    organizer: int = Field(gt=0, lt=2**52)
    month_limit: int = Field(ge=0, le=10000)
    quarter_limit: int = Field(ge=0, le=10000)


class EventManagement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    organizer: int = Field(gt=0, lt=2**52)
    month_limit: int = Field(ge=0, le=10000)
    quarter_limit: int = Field(ge=0, le=10000)
    archived: bool = Field(strict=True)


def can_manage_events(user):
    return bool(user.get('is_tamada') or user.get('is_owner') or
                user.get('is_developer') or user.get('role') in ('admin', 'developer'))


class Prize(BaseModel):
    request_id: UUID
    recipient: int | None = Field(default=None, gt=0, lt=2**52)
    days: Literal[30, 90]


def router_for(dsn, current_user, require_admin):
    router = APIRouter(prefix='/api/v1/contests')

    def db():
        return psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=3)

    def require_event_manager(user=Depends(current_user)):
        if not can_manage_events(user):
            raise HTTPException(403, 'Недостаточно прав для управления мероприятиями')
        return user

    def owned(conn, event_id, user):
        event = conn.execute('SELECT * FROM contest_events WHERE id=%s FOR UPDATE', (event_id,)).fetchone()
        if not event or (not can_manage_events(user) and event['organizer'] != user['telegram_user_id']):
            raise HTTPException(404, 'Мероприятие не найдено')
        return event

    def active(event):
        if event['archived_at'] is not None:
            raise HTTPException(409, 'Мероприятие в архиве')

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
            return conn.execute("""SELECT * FROM contest_events WHERE organizer=%s OR %s
                ORDER BY archived_at NULLS FIRST, created DESC""", (user['telegram_user_id'], can_manage_events(user))).fetchall()

    @router.post('')
    def allocate(data: Allocation, manager=Depends(require_event_manager)):
        with db() as conn:
            return conn.execute("""INSERT INTO contest_events(title,sender,greeting,organizer,month_limit,quarter_limit)
                VALUES (%(title)s,%(sender)s,%(greeting)s,%(organizer)s,%(month_limit)s,%(quarter_limit)s)
                RETURNING id""", data.model_dump()).fetchone()

    @router.get('/mine')
    def mine(user=Depends(current_user)):
        with db() as conn:
            return conn.execute("""SELECT p.id,p.days,p.expires,e.title,e.sender FROM contest_prizes p
                JOIN contest_events e ON e.id=p.event_id WHERE winner=%s ORDER BY p.created DESC""",(user['id'],)).fetchall()

    @router.get('/events/{event_id}')
    def details(event_id: UUID, user=Depends(current_user)):
        with db() as conn:
            event = owned(conn,event_id,user)
            prizes = conn.execute("""SELECT p.*,u.telegram_user_id AS winner_telegram_id
                FROM contest_prizes p LEFT JOIN users u ON u.id=p.winner
                WHERE p.event_id=%s ORDER BY p.created DESC""",(event_id,)).fetchall()
            return dict(event, prizes=prizes)

    @router.put('/events/{event_id}')
    def edit(event_id: UUID, data: EventText, user=Depends(current_user)):
        with db() as conn:
            active(owned(conn,event_id,user))
            conn.execute('UPDATE contest_events SET title=%s,sender=%s,greeting=%s WHERE id=%s',
                         (data.title,data.sender,data.greeting,event_id))
        return {'ok':True}

    @router.put('/events/{event_id}/management')
    def manage_event(event_id: UUID, data: EventManagement, user=Depends(require_event_manager)):
        with db() as conn:
            owned(conn, event_id, user)
            used = {row['days']: row['n'] for row in conn.execute(
                """SELECT days,count(*) AS n FROM contest_prizes
                   WHERE event_id=%s AND NOT revoked GROUP BY days""", (event_id,)).fetchall()}
            if data.month_limit < used.get(30, 0) or data.quarter_limit < used.get(90, 0):
                raise HTTPException(409, 'Лимит не может быть меньше числа выданных подарков')
            conn.execute("""UPDATE contest_events SET organizer=%s,month_limit=%s,quarter_limit=%s,
                archived_at=CASE WHEN %s THEN coalesce(archived_at,now()) ELSE NULL END WHERE id=%s""",
                (data.organizer,data.month_limit,data.quarter_limit,data.archived,event_id))
        return {'ok':True}

    @router.post('/events/{event_id}/prizes')
    def issue(event_id: UUID, data: Prize, user=Depends(current_user)):
        with db() as conn:
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
            return conn.execute('''INSERT INTO contest_prizes(event_id,request_id,recipient,days)
                VALUES (%s,%s,%s,%s) RETURNING *''',(event_id,data.request_id,data.recipient,data.days)).fetchone()

    @router.post('/events/{event_id}/prizes/{prize_id}/{action}')
    def manage(event_id: UUID, prize_id: UUID, action: Literal['share','revoke'], user=Depends(current_user)):
        with db() as conn:
            event = owned(conn,event_id,user)
            row = conn.execute('SELECT * FROM contest_prizes WHERE id=%s AND event_id=%s FOR UPDATE',
                               (prize_id,event_id)).fetchone()
            if not row or row['revoked'] or row['winner']:
                raise HTTPException(409,'Подарок недоступен')
            if action=='share':
                active(event)
            if action=='revoke':
                conn.execute('UPDATE contest_prizes SET revoked=true WHERE id=%s',(prize_id,))
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
        return {'id':prepared['id']}

    @router.get('/prizes/{prize_id}')
    def preview(prize_id: UUID, user=Depends(current_user)):
        with db() as conn:
            row = visible(conn,prize_id,user)
            return {key:row[key] for key in ('title','sender','greeting','days','expires')}

    @router.post('/prizes/{prize_id}/claim')
    def claim(prize_id: UUID, user=Depends(current_user)):
        with db() as conn:
            row = visible(conn,prize_id,user,lock=True)
            if row['expires']:
                return {'expires':row['expires']}
            conn.execute('UPDATE contest_prizes SET winner=%s WHERE id=%s',(user['id'],prize_id))
        # Keep the winner even if Shop commits and its HTTP response gets lost.
        reply = vpn_shop.exchange('gift',user,gift_id=str(prize_id),days=row['days'])
        expires = json.loads(reply.body).get('expires')
        if expires:
            with db() as conn:
                conn.execute('UPDATE contest_prizes SET expires=%s WHERE id=%s',(expires,prize_id))
        return {'expires':expires}

    return router
