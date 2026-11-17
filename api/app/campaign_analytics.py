"""Проверяемый путь: страница ссылки → приглашение → вступление Telegram.

Не выдаём посетителей ссылки за аудиторию MTProxy. Персональные Telegram ID,
IP и User-Agent не сохраняются. Хеш посетителя ограничен одной кампанией.
"""
import hashlib
import hmac
import html
import os
import re
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID
from urllib.parse import urlencode

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from . import channel_logos
from .bot_routes import _verify_bot_secret
from .gift_sharing import public_origin, throttle
from .stars_checkout import credentials
from .telegram_environment import is_test_environment

COOKIE='__Secure-pushok-analytics'
HEADERS={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}


def digest(value):
    key=os.getenv('SESSION_SIGNING_KEY','')
    if len(key)<32:raise HTTPException(503,'analytics_unavailable')
    return hmac.new(key.encode(),value.encode(),hashlib.sha256).hexdigest()


def visitor(raw):
    if not isinstance(raw,str) or not re.fullmatch(r'[a-f0-9]{32}\.[a-f0-9]{64}',raw):return None
    identity,signature=raw.split('.')
    return identity if hmac.compare_digest(signature,digest('visitor:'+identity)) else None


def member_payload(update):
    """Чистый whitelist, только переход из неучастника в участника по invite."""
    value=update.get('chat_member')
    if not isinstance(value,dict):return None
    old,new=value.get('old_chat_member',{}),value.get('new_chat_member',{})
    def inside(member):return member.get('status') in ('creator','administrator','member') or (member.get('status')=='restricted' and member.get('is_member') is True)
    if not isinstance(old,dict) or not isinstance(new,dict):return None
    user=new.get('user',{})
    if not isinstance(user,dict):return None
    if inside(old) or not inside(new) or user.get('is_bot') is not False:return None
    invite=value.get('invite_link')
    if not isinstance(invite,dict):return None
    link=invite.get('invite_link')
    if not isinstance(link,str) or not re.fullmatch(r'https://t\.me/\+[A-Za-z0-9_-]{8,128}',link):return None
    return dict(chat_id=value.get('chat',{}).get('id'),telegram_user_id=user.get('id'),invite_link=link,date=value.get('date'))


class MemberJoin(BaseModel):
    chat_id:int=Field(lt=0,gt=-(2**52))
    telegram_user_id:int=Field(gt=0,lt=2**52)
    invite_link:str=Field(pattern=r'^https://t\.me/\+[A-Za-z0-9_-]{8,128}$')
    date:int=Field(gt=0,lt=2**32)


def router_for(dsn,current_user):
    router=APIRouter()
    def db():return psycopg.connect(dsn(),connect_timeout=3,row_factory=dict_row)
    def campaign(conn,cid,user,lock=False):
        c=conn.execute('''SELECT c.id,c.channel_id,c.archived_at,sc.archived_at AS channel_archived_at,sc.telegram_url FROM campaigns c
            JOIN sponsor_channels sc ON sc.id=c.channel_id
            WHERE c.id=%s AND (c.advertiser_user_id=%s OR %s)'''+(' FOR UPDATE OF c' if lock else ''),
            (cid,user['id'],user['role'] in ('admin','operator'))).fetchone()
        if not c:raise HTTPException(404,'campaign_not_found')
        return c
    def source(conn,token):
        if not re.fullmatch('[a-f0-9]{32}',token):raise HTTPException(404,'analytics_link_unavailable')
        row=conn.execute('''SELECT a.*,sc.title FROM campaign_analytics a JOIN campaigns c ON c.id=a.campaign_id
            JOIN sponsor_channels sc ON sc.id=c.channel_id WHERE a.token=%s
            AND c.archived_at IS NULL AND sc.archived_at IS NULL AND c.channel_id=a.channel_id AND sc.telegram_url=a.channel_url''',(token,)).fetchone()
        current_bot=os.getenv('TELEGRAM_BOT_TOKEN','').split(':',1)[0]
        if not row or row['test_environment']!=is_test_environment() or current_bot!=str(row['bot_id']):raise HTTPException(404,'analytics_link_unavailable')
        return row

    @router.get('/api/v1/campaigns/{cid}/analytics')
    def report(cid:UUID,response:Response,user=Depends(current_user)):
        response.headers.update(HEADERS)
        with db() as conn:
            c=campaign(conn,cid,user)
            a=conn.execute('SELECT * FROM campaign_analytics WHERE campaign_id=%s',(cid,)).fetchone()
            if not a:return dict(enabled=False,reach=None,link_views=None,clicks=None,joins=None,conversion_percent=None,days=[])
            valid=(a['channel_id']==c['channel_id'] and a['channel_url']==c['telegram_url'] and not c['channel_archived_at']
                   and a['test_environment']==is_test_environment() and os.getenv('TELEGRAM_BOT_TOKEN','').split(':',1)[0]==str(a['bot_id']))
            counts=conn.execute('''SELECT count(*) AS views,count(clicked_at) AS clicks FROM campaign_link_visitors WHERE campaign_id=%s''',(cid,)).fetchone()
            joins=conn.execute('SELECT count(*) AS n FROM campaign_link_joins WHERE campaign_id=%s',(cid,)).fetchone()['n']
            days=conn.execute('''WITH events AS (
                SELECT viewed_at AS at,1 AS views,0 AS clicks,0 AS joins FROM campaign_link_visitors WHERE campaign_id=%s
                UNION ALL SELECT clicked_at,0,1,0 FROM campaign_link_visitors WHERE campaign_id=%s AND clicked_at IS NOT NULL
                UNION ALL SELECT joined_at,0,0,1 FROM campaign_link_joins WHERE campaign_id=%s)
                SELECT (at AT TIME ZONE 'Europe/Moscow')::date AS day,sum(views) AS views,sum(clicks) AS clicks,sum(joins) AS joins
                FROM events GROUP BY 1 ORDER BY 1 DESC LIMIT 30''',(cid,cid,cid)).fetchall()
        rate=round(100*joins/counts['clicks'],1) if counts['clicks'] and joins<=counts['clicks'] else None
        return dict(enabled=valid,configured=True,reach=None,link_views=counts['views'],clicks=counts['clicks'],joins=joins,
                    conversion_percent=rate,days=days,enabled_at=a['enabled_at'],test_environment=a['test_environment'],
                    url=public_origin()+'/r/'+a['token'] if valid and not c['archived_at'] else None,
                    source='campaign_link_and_telegram_invite',timezone='Europe/Moscow',channel_url_snapshot=a['channel_url'])

    @router.post('/api/v1/campaigns/{cid}/analytics/enable')
    def enable(cid:UUID,response:Response,user=Depends(current_user)):
        response.headers.update(HEADERS)
        _verify_bot_secret(os.getenv('BOT_INTERNAL_SECRET'))
        public_origin() # Конфигурация проверяется до создания приглашения Telegram.
        throttle(user['id'])
        with db() as conn:
            c=campaign(conn,cid,user,True)
            if c['archived_at'] or c['channel_archived_at']:raise HTTPException(409,'analytics_archived')
            token,bot_id=credentials()
            existing=conn.execute('SELECT * FROM campaign_analytics WHERE campaign_id=%s',(cid,)).fetchone()
            if existing:
                if existing['channel_id']!=c['channel_id'] or existing['test_environment']!=is_test_environment() or existing['bot_id']!=bot_id or existing['channel_url']!=c['telegram_url']:raise HTTPException(409,'analytics_source_changed')
                return dict(enabled=True)
            match=re.fullmatch(r'https://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?',c['telegram_url'])
            if not match:raise HTTPException(409,'analytics_channel_unavailable')
            try:
                chat=channel_logos._call(token,'getChat',{'chat_id':'@'+match[1]})
                if not channel_logos.matches_channel(chat,match[1].lower()) or type(chat.get('id')) is not int or chat['id']>=0:raise ValueError()
                own=channel_logos._call(token,'getChatMember',{'chat_id':chat['id'],'user_id':bot_id})
                if own.get('status')!='administrator' or own.get('can_invite_users') is not True:raise ValueError()
                invite=channel_logos._call(token,'createChatInviteLink',{'chat_id':chat['id'],'name':'puShok '+str(cid)[:8]})
                link=invite.get('invite_link','')
                if not re.fullmatch(r'https://t\.me/\+[A-Za-z0-9_-]{8,128}',link):raise ValueError()
            except (OSError,ValueError,TypeError,KeyError):
                raise HTTPException(503,'analytics_bot_rights_required') from None
            conn.execute('''INSERT INTO campaign_analytics(campaign_id,token,channel_id,channel_url,attribution_secret,chat_id,bot_id,test_environment,invite_link)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(cid,secrets.token_hex(16),c['channel_id'],c['telegram_url'],secrets.token_hex(32),chat['id'],bot_id,is_test_environment(),link))
        return dict(enabled=True)

    @router.get('/r/{token}',response_class=HTMLResponse,include_in_schema=False)
    def landing(token:str,request:Request):
        with db() as conn:row=source(conn,token)
        raw=request.cookies.get(COOKIE);identity=visitor(raw)
        if not identity:
            identity=secrets.token_hex(16);raw=identity+'.'+digest('visitor:'+identity)
        stamp=str(int(time.time()));nonce=stamp+'.'+digest('action:'+token+':'+identity+':'+stamp)
        action='/r/'+token+'?'+urlencode({'nonce':nonce})
        title=html.escape(row['title']);script_nonce=secrets.token_hex(16)
        content=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{title} · puShok</title>
        <style nonce="{script_nonce}">body{{margin:0;background:#071625;color:#edf7fc;font:16px system-ui;display:grid;min-height:100dvh;place-items:center}}main{{max-width:420px;padding:24px}}h1{{font-size:24px;overflow-wrap:anywhere}}button{{border:0;border-radius:12px;padding:16px;width:100%;font:inherit;font-weight:650;background:#35d4c4;color:#052b33}}small{{display:block;margin-top:18px;color:#a7c5d4}}</style>
        <main><p>puShok · Telegram</p><h1>{title}</h1><form method="post" action="{html.escape(action,quote=True)}"><button>Открыть канал</button></form><small>Переход учитывается в статистике этой ссылки. IP и данные профиля здесь не сохраняются.</small></main>
        <script nonce="{script_nonce}">fetch({action!r}+'&event=view',{{method:'POST',credentials:'same-origin'}}).catch(()=>{{}})</script></html>'''
        response=HTMLResponse(content,headers=HEADERS|{'Content-Security-Policy':f"default-src 'none'; style-src 'nonce-{script_nonce}'; script-src 'nonce-{script_nonce}'; connect-src 'self'; form-action 'self' https://t.me; base-uri 'none'; frame-ancestors 'none'"})
        response.set_cookie(COOKIE,raw,max_age=2592000,httponly=True,secure=True,samesite='lax',path='/r/')
        return response

    @router.post('/r/{token}',include_in_schema=False)
    def action(token:str,request:Request,nonce:str='',event:str='click'):
        identity=visitor(request.cookies.get(COOKIE))
        parts=nonce.split('.')
        if not identity or len(parts)!=2 or not parts[0].isdigit() or len(parts[0])>12:raise HTTPException(403,'analytics_invalid_action')
        stamp=int(parts[0])
        if not 0<=time.time()-stamp<=900 or not hmac.compare_digest(parts[1],digest('action:'+token+':'+identity+':'+parts[0])):raise HTTPException(403,'analytics_invalid_action')
        if event not in ('view','click'):raise HTTPException(422,'analytics_invalid_action')
        with db() as conn:
            row=source(conn,token)
            conn.execute('''INSERT INTO campaign_link_visitors(campaign_id,visitor_hash,clicked_at) VALUES(%s,%s,CASE WHEN %s THEN now() END)
                ON CONFLICT(campaign_id,visitor_hash) DO UPDATE SET clicked_at=coalesce(campaign_link_visitors.clicked_at,EXCLUDED.clicked_at)''',
                (row['campaign_id'],digest('link:'+token+':'+identity),event=='click'))
        return Response(status_code=204,headers=HEADERS) if event=='view' else RedirectResponse(row['invite_link'],status_code=303,headers=HEADERS)

    @router.post('/api/v1/internal/bot/analytics/join',include_in_schema=False)
    def joined(payload:MemberJoin,x_pushok_bot_secret:str|None=Header(default=None),x_pushok_telegram_environment:str|None=Header(default=None)):
        _verify_bot_secret(x_pushok_bot_secret)
        expected='test' if is_test_environment() else 'production'
        if x_pushok_telegram_environment!=expected:raise HTTPException(409,'telegram_environment_mismatch')
        token,bot_id=credentials()
        with db() as conn:
            row=conn.execute('''SELECT a.*,c.archived_at FROM campaign_analytics a JOIN campaigns c ON c.id=a.campaign_id WHERE invite_link=%s AND chat_id=%s AND bot_id=%s AND test_environment=%s''',
                (payload.invite_link,payload.chat_id,bot_id,is_test_environment())).fetchone()
            if not row:return dict(status='ignored')
            at=datetime.fromtimestamp(payload.date,timezone.utc)
            if payload.date<int(row['enabled_at'].timestamp()) or payload.date>time.time()+300 or (row['archived_at'] and payload.date>int(row['archived_at'].timestamp())):return dict(status='ignored')
            conn.execute('''INSERT INTO campaign_link_joins(campaign_id,user_hash,joined_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING''',
                (row['campaign_id'],hmac.new(row['attribution_secret'].encode(),str(payload.telegram_user_id).encode(),hashlib.sha256).hexdigest(),at))
        return dict(status='recorded')
    return router
