#!/usr/bin/env python3
"""Один запрос владельца к @MTProxyBot. JSON только через приватные pipes агента."""
import asyncio
import json
import logging
import os
import sys

from mtproxy_bot import MTProxyDialog, BotProtocolError
from telegram_owner import PrivateState, DEFAULT_STATE, DEVICE_NAME, TELETHON_VERSION, OwnerError, call


async def run(client, existing, request):
    try:
        await call(client.connect())
        me = await call(client.get_me())
        if me is None:
            raise OwnerError('revoked')
        if getattr(me, 'bot', False) or me.id != existing['user_id']:
            raise OwnerError('account_mismatch')
        if request.get('owner_telegram_id') and request['owner_telegram_id'] != me.id:
            raise OwnerError('account_mismatch')
        bot = await call(client.get_entity('MTProxyBot'))
        if not bot.bot or str(bot.username).lower() != 'mtproxybot':
            raise OwnerError('unexpected_mtproxy_bot')
        result = await asyncio.wait_for(MTProxyDialog(client, bot).prepare(request), 100)
        return dict(result, owner_telegram_id=me.id)
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), 5)
        except Exception:
            pass


def main():
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    try:
        import telethon
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        if telethon.__version__ != TELETHON_VERSION:
            raise OwnerError('owner_helper_unavailable')
        request = json.loads(sys.stdin.read(8192))
        if request.get('action') not in ('inspect','bind'):
            raise OwnerError('owner_helper_unavailable')
        with PrivateState(DEFAULT_STATE) as state:
            existing = state.read()
            if not existing:
                raise OwnerError('not_connected')
            async def perform():
                client = TelegramClient(StringSession(existing['session']), existing['api_id'], existing['api_hash'],
                    device_model=DEVICE_NAME, app_version='0.2', lang_code='en', system_lang_code='en',
                    receive_updates=False, catch_up=False, request_retries=0, connection_retries=1,
                    auto_reconnect=False, flood_sleep_threshold=0, timeout=10)
                result = await run(client, existing, request)
                state.save(dict(existing, session=client.session.save()))
                return result
            result = asyncio.run(perform())
        print(json.dumps(dict(ok=True, **result)))
    except Exception as exc:
        code = str(exc) if isinstance(exc, (BotProtocolError, OwnerError)) else 'telegram_unavailable'
        delay = 300
        if type(exc).__name__ == 'FloodWaitError':
            code, delay = 'telegram_flood_wait', max(300, int(exc.seconds))
        # Никаких сырых сообщений Telegram, телефонных номеров или сессий.
        print(json.dumps(dict(ok=False, code=code, retry_seconds=delay)))


if __name__ == '__main__':
    main()
