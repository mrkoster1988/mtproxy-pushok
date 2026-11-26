"""Deliver committed VPN events to their linked Telegram account."""
import logging
from html import escape
from urllib.error import HTTPError

from .telegram_environment import is_test_environment

log = logging.getLogger('pushok.bot.vpn')


def resume_on_start(update, api, username):
    if is_test_environment():
        return
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    actor = message.get('from') or {}
    words = (message.get('text') or '').lower().split(maxsplit=1)
    if not words or words[0] not in ('/start', '/start@' + username.lower()):
        return
    if (chat.get('type') != 'private' or type(actor.get('id')) is not int
            or actor['id'] <= 0 or chat.get('id') != actor['id'] or actor.get('is_bot')):
        return
    try:
        api('vpn-reminders/resume', {'telegram_user_id':actor['id']})
    except Exception as error:
        log.warning('VPN chat resume unavailable: %s', type(error).__name__)


def deliver(api, send, launch='https://pushok.shop/miniapp', queue='vpn-notices'):
    if is_test_environment():
        return
    try:
        items = api(queue+'/claim', {})['items']
    except Exception as error:
        log.warning('VPN notice queue unavailable: %s',type(error).__name__)
        return
    for item in items[:5]:
        try:
            if item['renew']:
                button = {'text':'Продлить подписку','web_app':{'url':launch+'?vpn=renew'}}
            else:
                button = {'text':'Открыть VPN' if item.get('connect') else 'Моя подписка','web_app':{'url':launch+'?vpn=home'}}
            titles={'expiry':'⏳ Сколько ещё работает VPN?','never':'👋 Первый выход с Пушком','idle':'🐾 Пушок на связи',
                    'created':'✨ У вас новая подписка','changed':'🐾 Что нового с вашей подпиской','order':'🧾 Новости по заказу',
                    'refund':'↩️ Возврат','device':'📱 Профиль устройства','removed':'🗂 Подписка удалена',
                    'link':'🔗 Ссылка подключения','reminder':'⏳ Срок подписки'}
            title=titles.get(item.get('category'),'🔔 VPN-подписка')
            body=item.get('formatted_body') if queue=='vpn-reminders' else None
            body=body or escape(item['text'].removeprefix('Pushok ИИ').strip())
            send({'chat_id':item['telegram_id'],'text':f'<b>{title}</b>\n\n{body}\n\n<i>Pushok ИИ</i>',
                  'parse_mode':'HTML',
                  'reply_markup':{'inline_keyboard':[[button]]}})
            status = 'sent'
        except HTTPError as error:
            status = 'blocked' if error.code == 403 else 'retry'
        except Exception:
            status = 'retry'
        try:
            api(queue+'/finish',{'id':item['id'],'lease':item['lease'],'status':status})
        except Exception as error:
            log.warning('VPN notice acknowledgement unavailable: %s',type(error).__name__)
