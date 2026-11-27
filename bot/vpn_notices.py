"""Deliver committed VPN events to their linked Telegram account."""
import json
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
                button = ({'text':'Открыть VPN','web_app':{'url':launch+'?vpn=home'}} if item.get('connect')
                          else {'text':'Показать подписку', 'callback_data':'subscription_widget'})
            titles={'premium':'💛 Подарок от Пушка','expiry':'⏳ Сколько ещё работает VPN?','never':'👋 Первый выход с Пушком','idle':'🐾 Пушок на связи',
                    'created':'✨ У вас новая подписка','changed':'🐾 Новости вашей подписки','renewal_plan':'🎟 Новый тариф','order':'🧾 Новости по заказу',
                    'refund':'↩️ Возврат','device':'📱 Профиль устройства','removed':'🗂 Подписка удалена',
                    'link':'🔗 Ссылка подключения','reminder':'⏳ Срок подписки'}
            title=titles.get(item.get('category'),'🔔 VPN-подписка')
            body=item.get('formatted_body')
            body=body or escape(item['text'].removeprefix('Pushok ИИ').strip())
            if item.get('category')=='hall_gift':
                send({'chat_id':item['telegram_id'],'gift_days':item['days'],
                      'text':f"<b>{escape(item['text'])}</b>\n\n{item['rank']}-е место в рейтинге приглашений.\nПриглашено друзей: {item['score']}.",
                      'parse_mode':'HTML','reply_markup':{'inline_keyboard':[[
                          {'text':'Забрать подарок','callback_data':'flowgift:'+item['gift_id']},
                          {'text':'Мой рейтинг','web_app':{'url':launch+'#/hall'}}]]}})
            elif item.get('category')=='trial_gift':
                send({'chat_id':item['telegram_id'],'trial_gift':True,'text':body,
                      'parse_mode':'HTML','reply_markup':{'inline_keyboard':[[button]]}})
            else:
                send({'chat_id':item['telegram_id'],'text':f'<b>{title}</b>\n\n{body}\n\n<i>Команда Pushok 🐾</i>',
                  'parse_mode':'HTML','rich':True,
                  'reply_markup':{'inline_keyboard':[[button]]}})
            status = 'sent'
        except HTTPError as error:
            status = 'retry'
            try:
                detail = json.loads(error.read(4096)).get('detail')
            except (ValueError,AttributeError,TypeError,OSError):
                detail = None
            if error.code == 403 and detail == 'recipient_blocked':
                status = 'blocked'
            elif queue == 'vpn-notices' and error.code == 503 and detail == 'cabinet_bot_chat_not_started':
                status = 'waiting_chat'
        except Exception:
            status = 'retry'
        try:
            api(queue+'/finish',{'id':item['id'],'lease':item['lease'],'status':status})
        except Exception as error:
            log.warning('VPN notice acknowledgement unavailable: %s',type(error).__name__)
