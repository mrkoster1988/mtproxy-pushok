"""Проверка подписки на официальный канал и её административные настройки."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from . import control
from .channel_logos import NoRedirect
from .telegram_environment import bot_api_url, configured_bot_username

DEFAULT_CHANNEL_URL = 'https://t.me/pushok_proxy'
CHANNEL_URL_PATTERN = r'^https://t\.me/[A-Za-z][A-Za-z0-9_]{3,31}/?$'


class ChannelSubscriptionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    enabled: StrictBool
    channel_url: str = Field(min_length=10, max_length=120, pattern=CHANNEL_URL_PATTERN)

    @field_validator('channel_url', mode='before')
    @classmethod
    def normalize_channel(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{3,31}', value):
                value = 'https://t.me/' + value.lstrip('@')
            elif re.fullmatch(r't\.me/[A-Za-z][A-Za-z0-9_]{3,31}/?', value):
                value = 'https://' + value
        return value

    @field_validator('channel_url')
    @classmethod
    def reject_cabinet_bot(cls, value):
        username = value.rstrip('/').rsplit('/', 1)[-1]
        bot = configured_bot_username()
        if bot and username.lower() == bot.lower():
            raise ValueError('Укажите канал, а не username бота')
        return value.rstrip('/')


def read_settings():
    saved = control.read('channel-subscription.json', {})
    if not isinstance(saved, dict):
        saved = {}
    channel_url = saved.get('channel_url')
    if not isinstance(channel_url, str) or not channel_url.strip():
        channel_url = os.getenv('OFFICIAL_AD_CHANNEL_URL', DEFAULT_CHANNEL_URL)
    channel_url = channel_url.strip()
    if not re.fullmatch(CHANNEL_URL_PATTERN, channel_url):
        channel_url = DEFAULT_CHANNEL_URL
    enabled = saved.get('enabled', True)
    if type(enabled) is not bool:
        enabled = True
    return {'enabled': enabled, 'channel_url': channel_url}


def router_for(get_current_user, require_admin):
    router = APIRouter(prefix='/api/v1', tags=['account'])

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

    @router.get('/admin/channel-subscription-settings')
    def get_channel_subscription_settings(user=Depends(require_admin)):
        return read_settings()

    @router.put('/admin/channel-subscription-settings')
    def put_channel_subscription_settings(settings: ChannelSubscriptionSettings,
                                          user=Depends(require_admin)):
        saved = settings.model_dump()
        control.write('channel-subscription.json', saved)
        return saved

    @router.get('/me/channel-subscription')
    def channel_subscription(response: Response, user=Depends(get_current_user)):
        response.headers['Cache-Control'] = 'private, no-store'
        settings = read_settings()
        channel_url = settings['channel_url']
        if not settings['enabled']:
            return {'required': False, 'joined': True, 'channel_url': channel_url,
                    'channel_title': 'Канал Pushok'}
        match = re.fullmatch(CHANNEL_URL_PATTERN, channel_url)
        telegram_id = user.get('telegram_user_id') if isinstance(user, dict) else None
        if not match or type(telegram_id) is not int or telegram_id <= 0:
            raise HTTPException(503, 'channel_subscription_unavailable')
        username = channel_url.rstrip('/').rsplit('/', 1)[-1]
        chat = telegram('getChat', {'chat_id': '@' + username})
        if not isinstance(chat, dict) or chat.get('type') != 'channel' or str(chat.get('username', '')).lower() != username.lower():
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
        return {'required': True, 'joined': joined, 'channel_url': channel_url,
                'channel_title': chat.get('title') or 'Канал Pushok'}

    return router
