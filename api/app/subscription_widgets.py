"""One private subscription message per account, refreshed from the cabinet projection."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException

from . import service_messages as telegram
from .bot_routes import _verify_bot_secret
from .vpn_cabinet import observations
from .vpn_notices import ChatOpened
from .telegram_environment import is_test_environment

HTML = """<!doctype html><meta charset=utf-8><style>
body{font:15px system-ui;margin:14px;color:var(--tg-theme-text-color,currentColor)}
h3{margin:0 0 7px}p{margin:4px 0}small{opacity:.8}
</style><h3></h3><p></p><small></small><script>
const w=window.TelegramWidget;
function draw(){const [plan,term,usage]=JSON.parse(w.chatData||'["","",""]');
for(const [tag,value] of [['h3',plan],['p',term],['small',usage]])document.querySelector(tag).textContent=value}
w.onEvent('chatDataChanged',draw);draw()
</script>"""


def traffic(value):
    if not isinstance(value, int) or value < 0:
        return '—'
    for unit in ('Б','КБ','МБ','ГБ','ТБ'):
        if value < 1024 or unit == 'ТБ':
            return f'{value:.1f} {unit}' if unit != 'Б' else f'{value} Б'
        value /= 1024


def snapshot(row):
    if not row:
        return ['Моя подписка', 'Подписки пока нет', '']
    active = row.get('state') == 'active'
    title = (row.get('tariff_name') or row.get('renewal_name') or 'Pushok Flow')[:64]
    states = {'revoked':'Доступ отозван','expired':'Срок истёк','pending':'Готовим доступ'}
    term = 'Доступ активен' if active else states.get(row.get('state'),'Статус уточняется')
    if active and row.get('is_premium'):
        title,term = '💛 Premium', 'Вам подарили бессрочный доступ. Расходы берём на себя!'
    elif active and row.get('expires_at'):
        term = 'До '+datetime.fromtimestamp(row['expires_at'],ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y, %H:%M МСК')
    return [title,term,'↓ '+traffic(row.get('received'))+'   ↑ '+traffic(row.get('sent'))]


def refresh(dsn, uid=None):
    if is_test_environment():
        return
    with psycopg.connect(dsn,row_factory=dict_row) as db:
        if uid:
            db.execute("""INSERT INTO subscription_widgets(telegram_id) VALUES(%s)
                ON CONFLICT (telegram_id) DO UPDATE SET suspended=false,due_at=now()""",(uid,))
            db.commit()
        row=db.execute("""SELECT * FROM subscription_widgets WHERE NOT suspended AND due_at<=now()
            AND (%s::bigint IS NULL OR telegram_id=%s) ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED""",(uid,uid)).fetchone()
        if not row:return
        chat=row['telegram_id']
        user=db.execute('SELECT id FROM users WHERE telegram_user_id=%s AND blocked_at IS NULL',(chat,)).fetchone()
        try:
            data=snapshot(observations()['accounts'].get(str(user['id'])) if user else None)
        except HTTPException:
            db.execute("UPDATE subscription_widgets SET due_at=now()+interval '1 minute' WHERE telegram_id=%s",(chat,))
            return
        value=json.dumps(data,ensure_ascii=False,separators=(',',':'))
        token=os.environ.get('TELEGRAM_BOT_TOKEN','')
        telegram.verify_sender(token)
        mode,mid=row['format'],row['message_id']
        fields={'chat_id':chat}
        try:
            if mid and (uid or row['snapshot']!=value):
                edit='editMessageWidgetData' if mode=='widget' else 'editMessageText'
                try:
                    telegram.telegram_call(token,edit,dict(fields,message_id=mid,**(
                        {'chat_data':value} if mode=='widget' else {'text':'\n'.join(data)})))
                except HTTPException as exc:
                    if exc.detail!='widget_message_missing':raise
                    if not uid:
                        db.execute('UPDATE subscription_widgets SET message_id=NULL,suspended=true WHERE telegram_id=%s',(chat,))
                        return
                    mid=None
            if not mid:
                try:
                    result=telegram.telegram_call(token,'sendWidget',dict(fields,html=HTML,height=108,
                        chat_data=value,caption='Моя подписка · Pushok Flow',protect_content=True))
                    mode='widget'
                except HTTPException as exc:
                    if exc.detail!='widget_unsupported':raise
                    result=telegram.telegram_call(token,'sendMessage',dict(fields,text='\n'.join(data),protect_content=True))
                    mode='text'
                mid=result['message_id']
        except HTTPException as exc:
            # Only a new explicit request can repeat an initial send with an uncertain outcome.
            suspend=not mid or exc.detail in ('cabinet_bot_chat_not_started','recipient_blocked')
            db.execute("""UPDATE subscription_widgets SET suspended=%s,due_at=now()+interval '10 minutes'
                WHERE telegram_id=%s""",(suspend,chat))
            return
        db.execute("""UPDATE subscription_widgets SET message_id=%s,format=%s,snapshot=%s,
            due_at=now()+interval '5 minutes' WHERE telegram_id=%s""",(mid,mode,value,chat))


def router_for(dsn):
    router=APIRouter(prefix='/api/v1/internal/bot/subscription-widget',include_in_schema=False)
    @router.post('/open')
    def open_widget(body:ChatOpened,x_pushok_bot_secret:str|None=Header(default=None)):
        _verify_bot_secret(x_pushok_bot_secret)
        refresh(dsn(),body.telegram_user_id)
        return {'ok':True}
    @router.post('/refresh')
    def tick(x_pushok_bot_secret:str|None=Header(default=None)):
        _verify_bot_secret(x_pushok_bot_secret)
        refresh(dsn())
        return {'ok':True}
    return router
