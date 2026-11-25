"""Deliver committed VPN events to their linked Telegram account."""
import logging
from urllib.error import HTTPError

from .telegram_environment import is_test_environment

log = logging.getLogger('pushok.bot.vpn')


def deliver(api, send):
    if is_test_environment():
        return
    try:
        items = api('vpn-notices/claim', {})['items']
    except Exception as error:
        log.warning('VPN notice queue unavailable: %s',type(error).__name__)
        return
    for item in items[:5]:
        try:
            if item['renew']:
                button = {'text':'Продлить подписку','url':'https://pushok.shop/shop/?renew=wg#plans'}
            else:
                button = {'text':'Моя подписка','web_app':{'url':'https://pushok.shop/#/vpn'}}
            send({'chat_id':item['telegram_id'],'text':item['text'],
                  'reply_markup':{'inline_keyboard':[[button]]}})
            status = 'sent'
        except HTTPError as error:
            status = 'blocked' if error.code == 403 else 'retry'
        except Exception:
            status = 'retry'
        try:
            api('vpn-notices/finish',{'id':item['id'],'lease':item['lease'],'status':status})
        except Exception as error:
            log.warning('VPN notice acknowledgement unavailable: %s',type(error).__name__)
