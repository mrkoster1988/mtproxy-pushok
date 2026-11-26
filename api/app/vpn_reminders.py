"""Choose due notices from fresh observations. Persist the lease before Telegram delivery."""
from datetime import datetime
import os
import html
import re
import secrets
import time
from zoneinfo import ZoneInfo
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field
from .bot_routes import _verify_bot_secret
from .vpn_cabinet import observations
from .vpn_notices import Receipt
from .vpn_settings import read, private

DAY=86400
router=APIRouter(prefix='/api/v1/internal/bot/vpn-reminders',include_in_schema=False)


def format_body(template,fields=None):
    # Parse only template formatting. Insert account data as literal Telegram text.
    parts=re.split(r'(\*\*[^*\n]+\*\*|__[^_\n]+__)',template)
    output=[]
    for i,part in enumerate(parts):
        tag=('b' if part.startswith('**') else 'i') if i%2 else None
        source=part[2:-2] if tag else part
        value=html.escape(source.format_map(fields) if fields is not None else source)
        output.append(f'<{tag}>{value}</{tag}>' if tag else value)
    return ''.join(output)


def days_label(days):
    last = days % 10
    word = 'дней' if 11 <= days % 100 <= 14 else 'день' if last == 1 else 'дня' if 2 <= last <= 4 else 'дней'
    return f'{days} {word}'


def due(p,row,now):
    sub=row['sub']
    if sub and sub.get('state')=='active':
        end=sub.get('expires_at') or 0
        if end<=now:return
        days=(end-now+DAY-1)//DAY
        thresholds=[d for d in p['expiry_days'] if days<=d]
        if p['expiry_enabled'] and thresholds:
            stage=min(thresholds)
            if not (sub.get('legacy_reminder') and stage==1):
                yield 'expiry',f"{sub['subscription_id']}:{end}:{stage}",0,days,end
    unissued = sub and sub.get('first_profile_unissued') and sub.get('state') in ('pending','expired')
    if sub and not unissued and (sub.get('state')!='active' or not sub.get('activity_known')):return
    seen=row['last_seen']
    if seen and sub and sub.get('state')=='active' and p['idle_enabled']:
        days=(now-seen)//DAY
        if days>=p['idle_days']:
            yield 'idle',str(seen),p['idle_repeat'],days,seen
    elif not row['ever_used'] and p['never_enabled']:
        days=(now-row['registered'])//DAY
        if days>=p['never_days']:
            yield 'never','registration',p['never_repeat'],days,row['registered']


def claim():
    data=observations()
    now=int(time.time())
    if data.get('activity_schema')!=1 or not 0<=now-data['sampled_at']<=180:return []
    unlinked=set(data.get('unlinked_telegram_ids',[]))
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(77904255) AS ok').fetchone()['ok']:return []
        policy=read(conn)['policy']
        users=conn.execute("""SELECT u.id,u.telegram_user_id,u.first_name,
            extract(epoch from u.created_at)::bigint AS registered,coalesce(a.last_seen,0) AS last_seen,
            coalesce(a.ever_used,false) AS ever_used FROM users u LEFT JOIN vpn_recipient_activity a ON a.user_id=u.id
            WHERE u.telegram_user_id>0 AND u.blocked_at IS NULL ORDER BY a.checked_at NULLS FIRST,u.id LIMIT 500""").fetchall()
        items=[]
        for row in users:
            sub=data['accounts'].get(str(row['id'])) or data.get('unlinked_activity',{}).get(str(row['telegram_user_id']))
            row['sub']=sub
            if sub:
                seen=sub.get('activity_seen') or 0
                if seen>row['last_seen']+120:row['last_seen']=seen
                row['ever_used']=row['ever_used'] or bool(row['last_seen'] or sub.get('ever_used'))
            conn.execute("""INSERT INTO vpn_recipient_activity(user_id,last_seen,ever_used) VALUES(%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen,ever_used=excluded.ever_used,checked_at=now()""",
                (row['id'],row['last_seen'],row['ever_used']))
            if not sub and row['telegram_user_id'] in unlinked:continue
            for kind,anchor,repeat,days,stamp in due(policy,row,now):
                lease=secrets.token_hex(16)
                event=conn.execute("""INSERT INTO vpn_scheduled_notices(user_id,kind,anchor,lease,next_attempt)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id,kind) DO UPDATE SET
                    anchor=excluded.anchor,lease=excluded.lease,next_attempt=excluded.next_attempt,chat_reopened=false,
                    sent_at=CASE WHEN vpn_scheduled_notices.anchor=excluded.anchor THEN vpn_scheduled_notices.sent_at END
                    WHERE NOT vpn_scheduled_notices.blocked AND (vpn_scheduled_notices.anchor!=excluded.anchor OR
                    (vpn_scheduled_notices.next_attempt<=%s AND (vpn_scheduled_notices.sent_at IS NULL OR
                    (%s>0 AND vpn_scheduled_notices.sent_at+%s<=%s)))) RETURNING id""",
                    (row['id'],kind,anchor,lease,now+120,now,repeat,repeat*DAY,now)).fetchone()
                if event:
                    fields=dict(имя=row['first_name'] or 'друг',дней=days,срок=days_label(days),
                        дата=datetime.fromtimestamp(stamp,ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y'))
                    items.append({'id':event['id'],'lease':lease,'telegram_id':row['telegram_user_id'],
                        'category':kind,'formatted_body':format_body(policy[kind+'_text'],fields),
                        'text':'Pushok ИИ\n\n'+policy[kind+'_text'].format_map(fields),'renew':kind=='expiry','connect':kind!='expiry'})
                    break
            if len(items)>=5:break
        return items


@router.post('/claim')
def get(x_pushok_bot_secret:str|None=Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    return private({'items':claim()})


@router.post('/finish')
def finish(receipt:Receipt,x_pushok_bot_secret:str|None=Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    now=int(time.time())
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute("""UPDATE vpn_scheduled_notices SET lease=NULL,next_attempt=%s,blocked=(%s AND NOT chat_reopened),chat_reopened=false,
            sent_at=CASE WHEN %s THEN %s ELSE sent_at END WHERE id=%s AND lease=%s""",
            (now+300 if receipt.status=='retry' else now,receipt.status=='blocked',receipt.status=='sent',now,receipt.id,receipt.lease))
    return {'ok':True}


class ResumedChat(BaseModel):
    telegram_user_id: int = Field(gt=0, le=2**63-1, strict=True)


@router.post('/resume')
def resume(chat: ResumedChat, x_pushok_bot_secret: str | None = Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        resume_recipient(conn,chat.telegram_user_id)
    return {'ok':True}


def resume_recipient(conn,uid):
    # Preserve a live lease. A 403 from a send started before /start can arrive late.
    conn.execute("""UPDATE vpn_scheduled_notices n
            SET blocked=false, chat_reopened=(n.lease IS NOT NULL),
                next_attempt=CASE WHEN n.blocked THEN 0 ELSE n.next_attempt END
            FROM users u WHERE n.user_id=u.id AND u.telegram_user_id=%s
            AND u.blocked_at IS NULL AND (n.blocked OR n.lease IS NOT NULL)""",
            (uid,))
