"""Проверка добровольной подписки на официальный канал Pushok."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import APIRouter, Depends, HTTPException, Response

from .channel_logos import NoRedirect
from .telegram_environment import bot_api_url


def router_for(get_current_user):
    router = APIRouter(prefix='/api/v1/me', tags=['account'])

    def telegram(method, payload):
        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            raise HTTPException(503, 'channel_subscription_unavailable')
        request = Request(bot_api_url(token, method), data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('oversized_response')
            data = json.loads(raw)
        except (HTTPError, URLError, OSError, ValueError, TypeError):
            raise HTTPException(503, 'channel_subscription_unavailable') from None
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise HTTPException(503, 'channel_subscription_unavailable')
        return data.get('result')

    @router.get('/channel-subscription')
    def channel_subscription(response: Response, user=Depends(get_current_user)):
        response.headers['Cache-Control'] = 'private, no-store'
        channel_url = os.getenv('OFFICIAL_AD_CHANNEL_URL', 'https://t.me/pushok_proxy').strip()
        match = re.fullmatch(r'https://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?', channel_url)
        telegram_id = user.get('telegram_user_id') if isinstance(user, dict) else None
        if not match or type(telegram_id) is not int or telegram_id <= 0:
            raise HTTPException(503, 'channel_subscription_unavailable')
        chat = telegram('getChat', {'chat_id': '@' + match.group(1)})
        if not isinstance(chat, dict) or chat.get('type') != 'channel' or str(chat.get('username', '')).lower() != match.group(1).lower():
            raise HTTPException(503, 'channel_subscription_unavailable')
        bot = telegram('getMe', {})
        bot_id = bot.get('id') if isinstance(bot, dict) else None
        if type(bot_id) is not int or bot_id <= 0:
            raise HTTPException(503, 'channel_subscription_unavailable')
        bot_member = telegram('getChatMember', {'chat_id': chat['id'], 'user_id': bot_id})
        if not isinstance(bot_member, dict) or bot_member.get('status') not in ('creator', 'administrator'):
            raise HTTPException(503, 'channel_subscription_unavailable')
        member = telegram('getChatMember', {'chat_id': chat['id'], 'user_id': telegram_id})
        joined = isinstance(member, dict) and (
            member.get('status') in ('creator', 'administrator', 'member') or
            (member.get('status') == 'restricted' and member.get('is_member') is True)
        )
        return {'joined': joined, 'channel_url': channel_url, 'channel_title': chat.get('title') or 'Канал Pushok'}

    return router
