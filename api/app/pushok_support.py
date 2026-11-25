"""Private support threads shared by the cabinet and Pushok's dedicated Telegram bot."""
import json
import os
import re
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5
from urllib.error import HTTPError
from urllib.request import Request
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import channel_ai_connection as ai, channel_topic_suggestions as topics, control
from .telegram_environment import is_test_environment
from .vpn_settings import private

BOT = 'server_pushok_bot'
HANDLE = 'https://t.me/'+BOT
Category = Literal['question','problem','idea']


class Message(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_id:UUID
    text:str=Field(min_length=1,max_length=2000)
    category:Category='question'
    case_id:int|None=Field(default=None,gt=0)
    human:bool=False


class BotMessage(Message):
    telegram_id:int=Field(gt=0,lt=2**52)


class StaffMessage(BaseModel):
    text:str=Field(min_length=1,max_length=2000)
    request_id:UUID
    close:bool=False


class BotCredential(BaseModel):
    token:SecretStr


def staff(user):
    return bool(user.get('is_owner') or user.get('is_developer') or user['role'] in ('operator','moderator','admin'))


def developer(user):
    if not (user.get('is_owner') or user.get('is_developer')):raise HTTPException(403,'developer_required')


def token():
    return (control.read('support-bot-private.json',{}) or {}).get('token') or os.getenv('PUSHOK_SUPPORT_BOT_TOKEN','')


class RecipientUnavailable(RuntimeError):
    pass


def bot_call(key, method, fields):
    req = Request('https://api.telegram.org/bot' + key + '/' + method,
                  data=json.dumps(fields).encode(), headers={'Content-Type': 'application/json'})
    try:
        result = json.loads(topics.read_url(req, 8, 512000))
    except HTTPError as error:
        if method == 'sendMessage' and error.code in (400, 403):
            raise RecipientUnavailable('support_recipient_unavailable') from None
    except (OSError, ValueError):
        pass
    else:
        if result.get('ok') is True:
            return result['result']
    raise RuntimeError('support_bot_unavailable') from None


def answer(category,question,history,requested):
    if requested:return 'Передаю вопрос команде. Ответ сотрудника появится в этом чате.',True
    if category=='idea':return 'Записал предложение для команды Pushok. Какой результат вы хотели бы получить и в какой ситуации это пригодится?',True
    facts=('В Android Flow на главной кнопка подключения. В настройках доступны маршруты приложений, подписки и диагностика. '
           'В Mini App вкладка VPN показывает срок доступа. Кнопка «Продлить» ведёт к оплате Stars. '
           'Отзыв подписки отключает доступ. Администратор может выдать новый профиль. '
           'По расходу Stars, исправлению подписки и вопросам без проверенного ответа передавай вопрос сотруднику.')
    cfg=ai.active('user_support')
    if cfg:
        try:
            topics.reserve_ai_call()
            messages=[{'role':'system','content':'Ты Pushok ИИ, поддержка Pushok. Отвечай кратко по-русски. '
                       'Ты не выполняешь действий, не видишь платежей и ключей. Не запрашивай секреты. '
                       'Верни JSON с полями text и human (boolean). Если данных мало, попроси уточнение или поставь human=true. '+facts}]
            messages += [{'role':'assistant' if m['sender']!='user' else 'user','content':m['body'][:1200]} for m in history[-6:]]
            messages.append({'role':'user','content':question})
            payload=dict(model=cfg[1],messages=messages,response_format={'type':'json_object'},max_completion_tokens=320)
            req=Request('https://api.openai.com/v1/chat/completions',data=json.dumps(payload).encode(),headers={
                'Content-Type':'application/json','Authorization':'Bearer '+cfg[0]})
            raw=json.loads(topics.read_url(req,8,24000))
            result=json.loads(raw['choices'][0]['message']['content'])
            if isinstance(result.get('text'),str) and result['text'].strip() and type(result.get('human')) is bool:
                return result['text'].strip()[:2000],result['human']
        except Exception:pass
    text=question.casefold()
    if 'отозван' in text:return 'Отозванная подписка отключает прежний профиль. Передаю вопрос сотруднику, который может проверить доступ и выдать новый профиль.',True
    if any(word in text for word in ('оплат','звёзд','stars','списал')):return 'Для продления откройте VPN → Продлить. Если Stars уже списались, пришлите номер заказа. Передаю обращение сотруднику для проверки.',True
    if 'трафик' in text:return 'В Mini App расход подписки виден во вкладке VPN. Расход по приложениям телефона можно посмотреть в Pushok Flow → Настройки → IP и трафик.',False
    return 'Обращение принято. Уточните, пожалуйста, что вы делали и что увидели на экране. Сотрудник сможет ответить в этой переписке.',True


def connection(dsn):
    return psycopg.connect(dsn,connect_timeout=3,row_factory=dict_row)


def post(dsn,uid,msg):
    key=str(msg.request_id)
    with connection(dsn) as db:
        db.execute('SELECT pg_advisory_xact_lock(%s)',(uid,))
        old=db.execute('SELECT conversation_id,reply FROM support_chat_intents WHERE telegram_id=%s AND request_key=%s',(uid,key)).fetchone()
        if old:return {'id':old['conversation_id'],'reply':old['reply']}
        if db.execute("""SELECT count(*) AS n FROM support_chat_messages m JOIN support_conversations c ON c.id=m.conversation_id
                        WHERE c.telegram_id=%s AND m.sender='user' AND m.created_at>now()-interval '5 minutes'""",(uid,)).fetchone()['n']>=15:
            raise HTTPException(429,'support_rate_limited')
        cid=msg.case_id
        if cid:
            case=db.execute('SELECT id,category,state FROM support_conversations WHERE id=%s AND telegram_id=%s FOR UPDATE',(cid,uid)).fetchone()
            if not case:raise HTTPException(404,'case_not_found')
            category=case['category']
        else:
            category=msg.category
            cid=db.execute('INSERT INTO support_conversations(telegram_id,category) VALUES(%s,%s) RETURNING id',(uid,category)).fetchone()['id']
        history=list(reversed(db.execute('SELECT sender,body FROM support_chat_messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 6',(cid,)).fetchall()))
        reply,human=answer(category,msg.text,history,msg.human or bool(msg.case_id and case['state']=='waiting_staff'))
        db.execute("INSERT INTO support_chat_messages(conversation_id,sender,body) VALUES(%s,'user',%s),(%s,'ai-agent',%s)",(cid,msg.text,cid,reply))
        db.execute('UPDATE support_conversations SET updated_at=now(),state=%s WHERE id=%s',('waiting_staff' if human else 'open',cid))
        db.execute('INSERT INTO support_chat_intents VALUES(%s,%s,%s,%s)',(uid,key,cid,reply))
        return {'id':cid,'reply':reply}


def router_for(dsn,current_user):
    router=APIRouter(route_class=ai.PrivateRoute)
    @router.get('/ai-support',include_in_schema=False)
    def open_bot():return RedirectResponse(HANDLE,302)
    @router.get('/api/v1/support/cases')
    def cases(team:bool=False,user=Depends(current_user)):
        if team and not staff(user):raise HTTPException(403)
        with connection(dsn()) as db:
            where='' if team else 'WHERE telegram_id=%s'
            rows=db.execute('SELECT id,category,state,updated_at FROM support_conversations '+where+' ORDER BY updated_at DESC LIMIT 100',() if team else (user['telegram_user_id'],)).fetchall()
        return {'items':rows}
    @router.get('/api/v1/support/cases/{cid}')
    def detail(cid:int,user=Depends(current_user)):
        with connection(dsn()) as db:
            row=db.execute('SELECT telegram_id,state,category FROM support_conversations WHERE id=%s',(cid,)).fetchone()
            if not row or (row['telegram_id']!=user['telegram_user_id'] and not staff(user)):raise HTTPException(404)
            messages=db.execute('SELECT sender,body,created_at FROM support_chat_messages WHERE conversation_id=%s ORDER BY id',(cid,)).fetchall()
        return {'messages':messages,'state':row['state'],'can_reply_as_staff':staff(user) and row['telegram_id']!=user['telegram_user_id']}
    @router.post('/api/v1/support/cases')
    def create(msg:Message,user=Depends(current_user)):return post(dsn(),user['telegram_user_id'],msg)
    @router.post('/api/v1/support/cases/{cid}/staff')
    def respond(cid:int,msg:StaffMessage,user=Depends(current_user)):
        if not staff(user):raise HTTPException(403)
        with connection(dsn()) as db:
            case=db.execute('SELECT telegram_id FROM support_conversations WHERE id=%s FOR UPDATE',(cid,)).fetchone()
            if not case:raise HTTPException(404)
            key='staff:'+str(msg.request_id)
            saved=db.execute('INSERT INTO support_chat_intents VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_key',(user['telegram_user_id'],key,cid,msg.text)).fetchone()
            if saved:
                mid=db.execute("INSERT INTO support_chat_messages(conversation_id,sender,body) VALUES(%s,'staff',%s) RETURNING id",(cid,msg.text)).fetchone()['id']
                db.execute("UPDATE support_conversations SET state=%s,updated_at=now() WHERE id=%s",('closed' if msg.close else 'open',cid))
                db.execute('INSERT INTO support_bot_outbox(message_id,telegram_id) SELECT %s,%s WHERE EXISTS(SELECT 1 FROM support_bot_contacts WHERE telegram_id=%s)',(mid,case['telegram_id'],case['telegram_id']))
        return {'ok':True}
    @router.get('/api/v1/support/bot')
    def bot_status(user=Depends(current_user)):
        developer(user)
        return private({'username':BOT,'configured':bool(token())})
    @router.put('/api/v1/support/bot')
    def set_bot(body:BotCredential,user=Depends(current_user)):
        developer(user)
        val=body.token.get_secret_value().strip()
        if not re.fullmatch(r'[0-9]+:[a-zA-Z0-9_-]{25,250}',val):raise HTTPException(422,'support_bot_token_invalid')
        try:
            me=bot_call(val,'getMe',{})
            if me.get('username','').lower()!=BOT:raise HTTPException(409,'support_bot_wrong_name')
            if bot_call(val,'getWebhookInfo',{}).get('url'):raise HTTPException(409,'support_bot_has_webhook')
        except RuntimeError:raise HTTPException(503,'support_bot_unavailable') from None
        with control.locked():control.write('support-bot-private.json',{'token':val})
        return private({'username':BOT,'configured':True})
    return router


def handle_update(db, dsn, key, update):
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    uid = chat.get('id')
    if chat.get('type') != 'private' or type(uid) is not int or uid <= 0 or uid != sender.get('id') or sender.get('is_bot'):
        return

    body = (message.get('text') or '').strip()[:2000]
    db.execute('INSERT INTO support_bot_contacts(telegram_id) VALUES(%s) ON CONFLICT DO NOTHING', (uid,))
    categories = {'/idea': 'idea', '/problem': 'problem', '/question': 'question', '/human': 'problem'}
    command, _, remaining = body.partition(' ')
    remaining = remaining.strip()
    if body in ('/start', '/help') or not body:
        response = ('🐾 Я Pushok ИИ. Задайте вопрос о Flow или Mini App. Для новой идеи напишите /idea и предложение, '
                    'для сотрудника — /human и вопрос. /new начнёт отдельное обращение.')
    elif body == '/new':
        db.execute('UPDATE support_bot_contacts SET current_case=NULL WHERE telegram_id=%s', (uid,))
        response = 'Напишите вопрос, пожелание или описание проблемы.'
    elif command in categories and not remaining:
        response = 'Добавьте сообщение после команды, например «/idea Хочу видеть историю расхода».'
    else:
        cid = db.execute('SELECT current_case FROM support_bot_contacts WHERE telegram_id=%s', (uid,)).fetchone()['current_case']
        try:
            record = post(dsn, uid, Message(
                request_id=uuid5(NAMESPACE_URL, f'pushok:ai-bot:{uid}:{message["message_id"]}'),
                case_id=None if command in categories else cid, category=categories.get(command, 'question'),
                human=command == '/human', text=remaining if command in categories else body))
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            response = 'Получил много сообщений подряд. Попробуйте написать через пять минут.'
        else:
            db.execute('UPDATE support_bot_contacts SET current_case=%s WHERE telegram_id=%s', (record['id'], uid))
            response = 'Обращение №' + str(record['id']) + '\n\n' + record['reply']
    try:
        bot_call(key, 'sendMessage', {'chat_id': uid, 'text': response})
    except RecipientUnavailable:
        pass


def run_bot(dsn, stop):
    import logging
    log = logging.getLogger(__name__)
    verified_key = None
    while not stop.is_set():
        key = token()
        if not key or is_test_environment():
            stop.wait(15)
            continue
        try:
            with connection(dsn()) as db:
                if not db.execute('SELECT pg_try_advisory_lock(78661, 1) AS ok').fetchone()['ok']:
                    stop.wait(5)
                    continue
                if verified_key != key:
                    if bot_call(key, 'getMe', {}).get('username', '').lower() != BOT:
                        raise RuntimeError('bot_identity')
                    if bot_call(key, 'getWebhookInfo', {}).get('url'):
                        raise RuntimeError('bot_has_webhook')
                    verified_key = key
                cursor = control.read('support-bot-offset.json', {}) or {}
                updates = bot_call(key, 'getUpdates', {'offset': cursor.get('offset', 0), 'timeout': 2,
                                                       'limit': 10, 'allowed_updates': ['message']})
                for update in updates:
                    handle_update(db, dsn(), key, update)
                    db.commit()
                    control.write('support-bot-offset.json', {'offset': update['update_id'] + 1})
                pending = db.execute("""SELECT o.message_id,o.telegram_id,m.body FROM support_bot_outbox o
                    JOIN support_chat_messages m ON m.id=o.message_id WHERE NOT o.delivered AND o.retry_at<=now()
                    ORDER BY o.message_id LIMIT 10""").fetchall()
                for item in pending:
                    try:
                        bot_call(key, 'sendMessage', {'chat_id': item['telegram_id'],
                                                     'text': 'Ответ поддержки Pushok\n\n' + item['body']})
                    except RecipientUnavailable:
                        db.execute('DELETE FROM support_bot_outbox WHERE message_id=%s', (item['message_id'],))
                    except RuntimeError:
                        db.execute("UPDATE support_bot_outbox SET retry_at=now()+interval '15 minutes' WHERE message_id=%s", (item['message_id'],))
                    else:
                        db.execute('UPDATE support_bot_outbox SET delivered=true WHERE message_id=%s', (item['message_id'],))
                    db.commit()
        except Exception as exc:
            log.warning('Support bot poll: %s', type(exc).__name__)
            stop.wait(10)
        else:
            stop.wait(1)
