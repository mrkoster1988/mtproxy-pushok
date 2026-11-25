"""Deliver committed VPN events to their linked Telegram account."""
import logging
from html import escape
from urllib.error import HTTPError

from .telegram_environment import is_test_environment

log = logging.getLogger('pushok.bot.vpn')


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
            titles={'expiry':'⏳ Срок подписки','never':'👋 Первое подключение','idle':'💤 Давно без VPN',
                    'created':'✨ Подписка создана','changed':'🔔 Изменение подписки','order':'🧾 Заказ',
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
