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
from . import support_appearance as design, support_languages as lang
from .telegram_environment import is_test_environment
from .vpn_settings import private
from .vpn_reminders import format_body

BOT = 'server_pushok_bot'
BOT_FILE = 'support-bot-private.json'
Category = Literal['question','problem','idea']


class LanguageChoice(BaseModel):
    preference: lang.Preference


class Message(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_id:UUID
    text:str=Field(min_length=1,max_length=2000)
    category:Category='question'
    case_id:int|None=Field(default=None,gt=0)
    human:bool=False
    locale_hint:str=Field(default="", max_length=40)


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


def answer(category,question,history,requested,locale="ru"):
    style=design.Appearance(**design.read().get('settings', {}))
    playful=style.tone=='playful'
    if requested:
        return lang.tr(locale, 'handoff'), True
    if category == 'idea':
        return lang.tr(locale, 'idea'), True
    guide = lang.guided(question, history)
    if guide:
        key, human = guide
        return lang.tr(locale, key), human
    if question.strip().casefold().strip('¡!?. ') in ('привет','здравствуйте','добрый день','добрый вечер','доброе утро','hi','hello','hola','buenos días'):
        if playful and locale == 'ru':
            return 'Привет! Пушок на связи'+(' 🐾' if style.emoji else '!')+' Чем помочь?',False
        return lang.tr(locale, 'hello'),False
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
            messages=[{'role':'system','content':f'Ты Pushok ИИ, поддержка Pushok. Отвечай кратко на языке {lang.MODEL_LANGUAGE[locale]}. '
                       'Ты не выполняешь действий, не видишь платежей и ключей. Не запрашивай секреты. '
                       'Используй короткие абзацы, **жирное** и __курсив__. '+emoji_hint+' '+tone_hint+' Верни JSON с полями text и human (boolean). Если данных мало, попроси уточнение или поставь human=true. '+facts}]
            messages += [{'role':'assistant' if m['sender']!='user' else 'user','content':lang.for_model(m['body'])[:1200]} for m in history[-6:]]
            messages.append({'role':'user','content':lang.for_model(question)})
            payload=dict(model=cfg[1],messages=messages,response_format={'type':'json_object'},max_completion_tokens=320)
            req=Request('https://api.openai.com/v1/chat/completions',data=json.dumps(payload).encode(),headers={
                'Content-Type':'application/json','Authorization':'Bearer '+cfg[0]})
            raw=json.loads(topics.read_url(req,8,24000))
            result=json.loads(raw['choices'][0]['message']['content'])
            if isinstance(result.get('text'),str) and result['text'].strip() and type(result.get('human')) is bool:
                return result['text'].strip()[:2000],result['human']
        except Exception:pass
    return lang.tr(locale, 'fallback'),True


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
            case=db.execute('SELECT id,category,state FROM support_conversations WHERE id=%s AND telegram_id=%s FOR NO KEY UPDATE',(cid,uid)).fetchone()
            if not case:raise HTTPException(404,'case_not_found')
            category=case['category']
        else:
            category=msg.category
            cid=db.execute('INSERT INTO support_conversations(telegram_id,category) VALUES(%s,%s) RETURNING id',(uid,category)).fetchone()['id']
        history=list(reversed(db.execute('SELECT sender,body FROM support_chat_messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 6',(cid,)).fetchall()))
        locale = lang.effective(lang.get(db, uid), msg.locale_hint)
        if msg.case_id and case['state']=='waiting_staff' and not msg.human:
            reply,human=lang.tr(locale,'queued'),True
        else:
            reply,human=answer(category,msg.text,history,msg.human,locale)
        db.execute("INSERT INTO support_chat_messages(conversation_id,sender,body) VALUES(%s,'user',%s),(%s,'ai-agent',%s)",(cid,msg.text,cid,reply))
        db.execute('UPDATE support_conversations SET updated_at=now(),state=%s,reply_locale=%s WHERE id=%s',('waiting_staff' if human else 'open',locale,cid))
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
    @router.get('/api/v1/support/language')
    def language(user=Depends(current_user)):
        with connection(dsn()) as db:
            return {'preference':lang.get(db, user['telegram_user_id'])}

    @router.put('/api/v1/support/language')
    def change_language(body: LanguageChoice, user=Depends(current_user)):
        with connection(dsn()) as db:
            lang.save(db, user['telegram_user_id'], body.preference)
        return {'preference':body.preference}

    @router.get('/api/v1/support/cases')
    def cases(team:bool=False,user=Depends(current_user)):
        if team and not staff(user):raise HTTPException(403)
        with connection(dsn()) as db:
            where='' if team else 'WHERE telegram_id=%s'
            order = "(state='waiting_staff') DESC," if team else ''
            rows=db.execute("""SELECT id,category,state,updated_at,reply_locale,
                (SELECT left(body,80) FROM support_chat_messages WHERE conversation_id=c.id AND sender='user'
                 ORDER BY id LIMIT 1) AS subject FROM support_conversations c """+where+' ORDER BY '+order+'updated_at DESC,id DESC LIMIT 100',() if team else (user['telegram_user_id'],)).fetchall()
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
        language_action = action in {'lang:'+code for code in ('auto',*lang.NAMES)}
        if (action not in design.ACTIONS and not language_action) or callback.get('data') != 'support:' + action:
            return
        try:
            bot_call(key, 'answerCallbackQuery', {'callback_query_id': callback['id']})
        except CallbackExpired:
            pass
        body = '/language '+action[5:] if language_action else '/' + action
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
    hint = str(sender.get('language_code') or '')[:40]
    preference = lang.get(db, uid)
    if command == '/language':
        if remaining in ('auto', *lang.NAMES):
            lang.save(db, uid, remaining)
            preference = remaining
        locale = lang.effective(preference, hint)
        options = {'auto':lang.tr(locale,'auto'), **lang.NAMES}
        keyboard = [[{'text':('✓ ' if code == preference else '')+title,'callback_data':'support:lang:'+code}] for code,title in options.items()]
        bot_call(key,'sendMessage',{'chat_id':uid,'text':'🌐 '+lang.tr(locale,'lang'),'reply_markup':{'inline_keyboard':keyboard}})
        return
    locale = lang.effective(preference, hint)
    sections={'/subscriptions':('vpn','Моя подписка'),'/campaigns':('campaigns','Мои кампании'),
              '/tariffs':('vpn_renew','Тарифы VPN'),'/advertise':('create','Создать кампанию'),
              '/channels':('channels','Каналы'),'/moderation':('moderation','Модерация')}
    if command in sections:
        route,title=sections[command]
        menus={'en':('My subscription','My campaigns','VPN plans','Create campaign','Channels','Moderation'),
               'es':('Mi suscripción','Mis campañas','Planes VPN','Crear campaña','Canales','Moderación')}
        if locale in menus:
            title = menus[locale][list(sections).index(command)]
        from .service_messages import cabinet_link
        bot_call(key,'sendMessage',{'chat_id':uid,'text':title,
            'reply_markup':{'inline_keyboard':[[cabinet_link({'text':title,'callback_data':route})]]}})
        return
    state = design.read()
    style = design.Appearance(**state.get('settings', {}))
    style = style.model_copy(update=lang.APPEARANCE[locale])
    if command in ('/start', '/help') or not body:
        fields, path = design.welcome_payload(state, uid, command == '/help', locale)
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
                    return uid if command == '/start' else None
                except PhotoRejected:
                    pass
            bot_call(key, 'sendMessage', fields)
        except RecipientUnavailable:
            return
        return uid if command == '/start' else None
    if command == '/new':
        db.execute('UPDATE support_bot_contacts SET current_case=NULL,pending_action=NULL WHERE telegram_id=%s', (uid,))
        response = design.heading(style, '📝', lang.tr(locale,'new')) + '\n\n' + lang.tr(locale,'question')
    elif command in categories and not remaining and not (command == '/human' and contact['current_case']):
        action = command[1:]
        default = ('🛠', 'Опишите проблему и укажите, на каком шаге она возникает.')
        glyph, prompt = design.ACTIONS.get(action, default)
        prompt = prompt if locale == 'ru' else lang.tr(locale,'idea_prompt' if action == 'idea' else action)
        response = design.heading(style, glyph, getattr(style, action, lang.tr(locale,'problem'))) + '\n\n' + prompt
        db.execute('UPDATE support_bot_contacts SET pending_action=%s,current_case=NULL WHERE telegram_id=%s', (action, uid))
    else:
        cid = contact['current_case']
        if command not in categories and contact['pending_action']:
            command, remaining = '/' + contact['pending_action'], body
        if command == '/human' and not remaining:
            remaining = style.human
        event_id = 'callback:'+str(callback['id']) if callback else message['message_id']
        namespace = 'pushok:ai-bot:' if legacy else f'pushok:ai-bot:{identity}:'
        try:
            record = post(dsn, uid, Message(
                request_id=uuid5(NAMESPACE_URL, f'{namespace}{uid}:{event_id}'),
                case_id=cid if command == '/human' or command not in categories else None, locale_hint=hint, category=categories.get(command, 'question'),
                human=command == '/human', text=remaining if command in categories else body))
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            response = lang.tr(locale,'rate')
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
                    resumed = handle_update(db, dsn(), key, update, legacy)
                    db.commit()
                    if resumed:
                        from .vpn_notices import relay
                        try:
                            relay('resume-chat', {'telegram_id':resumed})
                        except HTTPException:
                            log.warning('Could not advance pending tariff notifications after bot start')
                    control.write(cursor_name, {'offset': update['update_id'] + 1})
                pending = db.execute("""SELECT o.message_id,o.telegram_id,m.body,c.reply_locale FROM support_bot_outbox o
                    JOIN support_chat_messages m ON m.id=o.message_id JOIN support_conversations c ON c.id=m.conversation_id WHERE NOT o.delivered AND o.retry_at<=now()
                    AND (o.bot_id=%s OR (o.bot_id=0 AND %s))
                    ORDER BY o.message_id LIMIT 10""", (identity, legacy)).fetchall()
                if pending:
                    style = design.Appearance(**design.read().get('settings', {}))
                for item in pending:
                    try:
                        bot_call(key, 'sendMessage', {'chat_id': item['telegram_id'],
                                                     'text': format_body(design.heading(style, '👤', lang.APPEARANCE[lang.effective(lang.get(db,item['telegram_id']),item['reply_locale'])].get('staff_title', style.staff_title)) + '\n\n' + item['body']), 'parse_mode': 'HTML'})
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
