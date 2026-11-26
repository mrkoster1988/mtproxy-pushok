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
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from . import channel_ai_connection as ai, channel_topic_suggestions as topics, control
from . import support_appearance as design
from .telegram_environment import is_test_environment
from .vpn_settings import private
from .vpn_reminders import format_body

BOT = 'server_pushok_bot'
BOT_FILE = 'support-bot-private.json'
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
    token: SecretStr | None = None
    username: str | None = None

    @field_validator('username')
    @classmethod
    def valid_username(cls, value):
        if value is None:
            return None
        value = value.strip().removeprefix('@').lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,28}bot', value):
            raise ValueError('invalid Telegram bot username')
        return value


class BotName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    username: str
    name: str = Field(min_length=1, max_length=64)


def staff(user):
    return bool(user.get('is_owner') or user.get('is_developer') or user['role'] in ('operator','moderator','admin'))


def developer(user):
    if not (user.get('is_owner') or user.get('is_developer')):raise HTTPException(403,'developer_required')


def credential():
    saved = control.read(BOT_FILE, {}) or {}
    return {**saved, 'username': saved.get('username', BOT),
            'token': saved.get('token') or os.getenv('PUSHOK_SUPPORT_BOT_TOKEN', '')}


def bot_id(key):
    prefix = key.partition(':')[0]
    return int(prefix) if prefix.isdecimal() and len(prefix) < 17 else 0


def public_bot(cfg):
    return {'username': cfg['username'], 'configured': bool(cfg['token']), 'display_name': cfg.get('display_name', '')}


def verify_bot(key, username):
    if not re.fullmatch(r'[0-9]{1,16}:[a-zA-Z0-9_-]{25,250}', key):
        raise HTTPException(422, 'support_bot_token_invalid')
    if bot_id(key) == bot_id(os.getenv('TELEGRAM_BOT_TOKEN', '')):
        raise HTTPException(409, 'support_bot_cabinet')
    me = bot_call(key, 'getMe', {})
    if me.get('username', '').lower() != username:
        raise HTTPException(409, 'support_bot_wrong_name')
    if bot_call(key, 'getWebhookInfo', {}).get('url'):
        raise HTTPException(409, 'support_bot_has_webhook')
    return me


class RecipientUnavailable(RuntimeError):
    pass


class PhotoRejected(RuntimeError):
    pass


class CallbackExpired(RuntimeError):
    pass


def bot_call(key, method, fields):
    req = Request('https://api.telegram.org/bot' + key + '/' + method,
                  data=json.dumps(fields).encode(), headers={'Content-Type': 'application/json'})
    try:
        result = json.loads(topics.read_url(req, 8, 512000))
    except HTTPError as error:
        if method == 'sendPhoto' and error.code == 400:
            raise PhotoRejected() from None
        if method == 'answerCallbackQuery' and error.code == 400:
            raise CallbackExpired() from None
        if method in ('sendPhoto', 'sendMessage') and error.code in (400, 403):
            refusal=RecipientUnavailable('support_recipient_unavailable')
            refusal.telegram_status=error.code
            raise refusal from None
    except (OSError, ValueError):
        pass
    else:
        if result.get('ok') is True:
            return result['result']
    raise RuntimeError('support_bot_unavailable') from None


def answer(category,question,history,requested):
    style=design.Appearance(**design.read().get('settings', {}))
    playful=style.tone=='playful'
    if requested:return 'Передал вопрос команде. Ответ сотрудника придёт прямо сюда.',True
    if category=='idea':
        opening='Вот это идея! ' if playful else 'Спасибо за предложение. '
        return opening+'Записал для команды Pushok. В какой момент эта функция была бы вам особенно полезна?',True
    if question.strip().casefold().rstrip('!.? ') in ('привет','здравствуйте','добрый день','добрый вечер','доброе утро'):
        return (('Привет! Пушок на связи'+(' 🐾' if style.emoji else '!')+' Что сегодня разбираем — Flow, Mini App или новую идею?') if playful
                else 'Здравствуйте! Чем помочь с Pushok Flow или Mini App?'),False
    facts=('В Android Flow на главной кнопка подключения. В настройках доступны маршруты приложений, подписки и диагностика. '
           'В Mini App вкладка VPN показывает срок доступа. Кнопка «Продлить» ведёт к оплате Stars. '
           'Отзыв подписки отключает доступ. Администратор может выдать новый профиль. '
           'По расходу Stars, исправлению подписки и вопросам без проверенного ответа передавай вопрос сотруднику.')
    cfg=ai.active('user_support')
    if cfg:
        try:
            topics.reserve_ai_call()
            emoji_hint = 'Уместны эмодзи.' if style.emoji else 'Пиши без эмодзи.'
            tone_hint=('Говори тепло и живо, как Пушок. Короткая кошачья шутка уместна в приветствии или разговоре об идеях. '
                       'При ошибках, платежах, отзыве и передаче сотруднику сразу переходи к делу.' if playful else
                       'Говори спокойно, прямо, без шуток и образных выражений.')
            messages=[{'role':'system','content':'Ты Pushok ИИ, поддержка Pushok. Отвечай кратко по-русски. '
                       'Ты не выполняешь действий, не видишь платежей и ключей. Не запрашивай секреты. '
                       'Используй короткие абзацы, **жирное** и __курсив__. '+emoji_hint+' '+tone_hint+' Верни JSON с полями text и human (boolean). Если данных мало, попроси уточнение или поставь human=true. '+facts}]
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
    if any(word in text for word in ('оплат','звёзд','stars','списал')):return 'Для продления откройте **VPN → Продлить**. Если Stars уже списались, пришлите номер заказа. Передаю обращение сотруднику для проверки.',True
    if 'трафик' in text:return 'В Mini App расход подписки виден во вкладке VPN. Расход по приложениям телефона можно посмотреть в **Pushok Flow → Настройки → IP и трафик**.',False
    return 'Давайте разберёмся вместе. На что вы нажали и что появилось на экране? Передал вопрос команде — ответ придёт в этот чат.',True


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
    def open_bot():
        return RedirectResponse('https://t.me/' + credential()['username'], 302, headers={'Cache-Control':'no-store'})

    @router.get('/ai-support/images/{image_id}.jpg', include_in_schema=False)
    def welcome_image(image_id: str):
        return design.photo(image_id)
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
                db.execute('INSERT INTO support_bot_outbox(message_id,telegram_id,bot_id) SELECT %s,telegram_id,bot_id FROM support_bot_contacts WHERE telegram_id=%s',(mid,case['telegram_id']))
        return {'ok':True}
    @router.get('/api/v1/support/bot')
    def bot_status(user=Depends(current_user)):
        developer(user)
        return private(public_bot(credential()))

    @router.put('/api/v1/support/bot')
    def set_bot(body: BotCredential, user=Depends(current_user)):
        developer(user)
        previous = credential()
        key = body.token.get_secret_value().strip() if body.token else previous['token']
        username = body.username or previous['username']
        try:
            me = verify_bot(key, username)
        except RuntimeError:
            raise HTTPException(503, 'support_bot_unavailable') from None
        with control.locked():
            if previous != credential():
                raise HTTPException(409, 'support_bot_settings_changed')
            cfg = dict(token=key, username=username, display_name=me.get('first_name', ''),
                       legacy_id=previous.get('legacy_id', bot_id(previous['token']) or bot_id(key)))
            control.write(BOT_FILE, cfg)
        return private(public_bot(cfg))

    @router.put('/api/v1/support/bot/name')
    def set_display_name(body: BotName, user=Depends(current_user)):
        developer(user)
        cfg = credential()
        if not cfg['token'] or cfg['username'] != body.username:
            raise HTTPException(409, 'support_bot_settings_changed')
        try:
            verify_bot(cfg['token'], cfg['username'])
            bot_call(cfg['token'], 'setMyName', {'name': body.name})
        except RuntimeError:
            raise HTTPException(503, 'support_bot_unavailable') from None
        with control.locked():
            if credential() != cfg:
                raise HTTPException(409, 'support_bot_settings_changed')
            cfg['display_name'] = body.name
            control.write(BOT_FILE, cfg)
        return private(public_bot(cfg))

    @router.get('/api/v1/support/bot/appearance')
    def appearance(user=Depends(current_user)):
        developer(user)
        return private(design.public(design.read()))

    @router.put('/api/v1/support/bot/appearance')
    def update_appearance(body: design.AppearanceEdit, user=Depends(current_user)):
        developer(user)
        return private(design.save(body))
    return router


def handle_update(db, dsn, key, update, legacy=True):
    callback = update.get('callback_query') or {}
    message = callback.get('message') or update.get('message') or {}
    chat = message.get('chat') or {}
    sender = callback.get('from') if callback else message.get('from')
    uid = chat.get('id')
    if (chat.get('type') != 'private' or type(uid) is not int or uid <= 0 or not sender
            or uid != sender.get('id') or sender.get('is_bot')):
        return
    if callback:
        action = callback.get('data', '').removeprefix('support:')
        if action not in design.ACTIONS or callback.get('data') != 'support:' + action:
            return
        try:
            bot_call(key, 'answerCallbackQuery', {'callback_query_id': callback['id']})
        except CallbackExpired:
            pass
        body = '/' + action
    else:
        body = (message.get('text') or '').strip()[:2000]

    identity = bot_id(key)
    contact = db.execute("""INSERT INTO support_bot_contacts(telegram_id,bot_id) VALUES(%s,%s)
        ON CONFLICT (telegram_id) DO UPDATE SET bot_id=excluded.bot_id,
        current_case=CASE WHEN support_bot_contacts.bot_id=excluded.bot_id OR
                         (support_bot_contacts.bot_id=0 AND %s) THEN support_bot_contacts.current_case END,
        pending_action=CASE WHEN support_bot_contacts.bot_id=excluded.bot_id THEN support_bot_contacts.pending_action END
        RETURNING current_case,pending_action""", (uid, identity, legacy)).fetchone()
    categories = {'/idea': 'idea', '/problem': 'problem', '/question': 'question', '/human': 'problem'}
    command, _, remaining = body.partition(' ')
    remaining = remaining.strip()
    sections={'/subscriptions':('vpn','Моя подписка'),'/campaigns':('campaigns','Мои кампании'),
              '/tariffs':('vpn_renew','Тарифы VPN'),'/advertise':('create','Создать кампанию'),
              '/channels':('channels','Каналы'),'/moderation':('moderation','Модерация')}
    if command in sections:
        route,title=sections[command]
        from .service_messages import cabinet_link
        bot_call(key,'sendMessage',{'chat_id':uid,'text':title,
            'reply_markup':{'inline_keyboard':[[cabinet_link({'text':title,'callback_data':route})]]}})
        return
    state = design.read()
    style = design.Appearance(**state.get('settings', {}))
    if command in ('/start', '/help') or not body:
        fields, path = design.welcome_payload(state, uid, command == '/help')
        if command == '/start':
            from .vpn_reminders import resume_recipient
            resume_recipient(db,uid)
            db.execute('UPDATE support_bot_contacts SET pending_action=NULL WHERE telegram_id=%s', (uid,))
        try:
            if path:
                caption = dict(fields, photo=design.photo_url(path))
                caption['caption'] = caption.pop('text')
                try:
                    bot_call(key, 'sendPhoto', caption)
                    return
                except PhotoRejected:
                    pass
            bot_call(key, 'sendMessage', fields)
        except RecipientUnavailable:
            pass
        return
    if command == '/new':
        db.execute('UPDATE support_bot_contacts SET current_case=NULL,pending_action=NULL WHERE telegram_id=%s', (uid,))
        response = design.heading(style, '📝', 'Новое обращение') + '\n\nНапишите вопрос, пожелание или описание проблемы.'
    elif command in categories and not remaining:
        action = command[1:]
        default = ('🛠', 'Опишите проблему и укажите, на каком шаге она возникает.')
        glyph, prompt = design.ACTIONS.get(action, default)
        response = design.heading(style, glyph, getattr(style, action, 'Сообщить о проблеме')) + '\n\n' + prompt
        db.execute('UPDATE support_bot_contacts SET pending_action=%s,current_case=NULL WHERE telegram_id=%s', (action, uid))
    else:
        cid = contact['current_case']
        if command not in categories and contact['pending_action']:
            command, remaining = '/' + contact['pending_action'], body
        namespace = 'pushok:ai-bot:' if legacy else f'pushok:ai-bot:{identity}:'
        try:
            record = post(dsn, uid, Message(
                request_id=uuid5(NAMESPACE_URL, f'{namespace}{uid}:{message["message_id"]}'),
                case_id=None if command in categories else cid, category=categories.get(command, 'question'),
                human=command == '/human', text=remaining if command in categories else body))
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            response = 'Дайте мне пять минут разобрать сообщения. После этого можно будет написать снова.'
        else:
            db.execute('UPDATE support_bot_contacts SET current_case=%s,pending_action=NULL WHERE telegram_id=%s', (record['id'], uid))
            response = design.heading(style, '💬', style.case_title + ' №' + str(record['id'])) + '\n\n' + record['reply']
    try:
        bot_call(key, 'sendMessage', {'chat_id': uid, 'text': format_body(response), 'parse_mode': 'HTML'})
    except RecipientUnavailable:
        pass


def run_bot(dsn, stop):
    import logging
    log = logging.getLogger(__name__)
    verified_key = None
    while not stop.is_set():
        cfg = credential()
        key = cfg['token']
        identity = bot_id(key)
        if not key or is_test_environment():
            stop.wait(15)
            continue
        try:
            with connection(dsn()) as db:
                if not db.execute('SELECT pg_try_advisory_lock(78661, 1) AS ok').fetchone()['ok']:
                    stop.wait(5)
                    continue
                if verified_key != (key, cfg['username']):
                    verify_bot(key, cfg['username'])
                    verified_key = (key, cfg['username'])
                legacy = cfg.get('legacy_id', identity) == identity
                cursor_name = f'support-bot-offset-{identity}.json'
                cursor = control.read(cursor_name)
                if cursor is None:
                    cursor = control.read('support-bot-offset.json', {}) if legacy else {}
                updates = bot_call(key, 'getUpdates', {'offset': cursor.get('offset', 0), 'timeout': 2,
                                                       'limit': 10, 'allowed_updates': ['message', 'callback_query']})
                for update in updates:
                    handle_update(db, dsn(), key, update, legacy)
                    db.commit()
                    control.write(cursor_name, {'offset': update['update_id'] + 1})
                pending = db.execute("""SELECT o.message_id,o.telegram_id,m.body FROM support_bot_outbox o
                    JOIN support_chat_messages m ON m.id=o.message_id WHERE NOT o.delivered AND o.retry_at<=now()
                    AND (o.bot_id=%s OR (o.bot_id=0 AND %s))
                    ORDER BY o.message_id LIMIT 10""", (identity, legacy)).fetchall()
                if pending:
                    style = design.Appearance(**design.read().get('settings', {}))
                for item in pending:
                    try:
                        bot_call(key, 'sendMessage', {'chat_id': item['telegram_id'],
                                                     'text': format_body(design.heading(style, '👤', style.staff_title) + '\n\n' + item['body']), 'parse_mode': 'HTML'})
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
