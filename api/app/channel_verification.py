"""Подтверждение прав администратора канала через официальный Telegram Bot API.

Бот должен быть администратором канала, а отправитель — владельцем либо
администратором. Не меняет права, настройки канала или сам MTProxy.
"""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ChannelNotVerified(Exception):
    """Нельзя подтвердить указанный канал и права администратора."""


class TelegramUnavailable(Exception):
    """Telegram API или конфигурация не позволяют выполнить проверку."""


def _telegram_call(token: str, method: str, payload: dict) -> dict:
    request = Request(
        "https://api.telegram.org/bot" + token + "/" + method,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=6) as connection:
            reply = json.load(connection)
    except HTTPError as exc:
        # Нельзя выводить str(exc) — URL ошибки может содержать токен.
        if exc.code in (400, 403, 404):
            raise ChannelNotVerified("channel_not_accessible") from None
        raise TelegramUnavailable("telegram_api_unavailable") from None
    except (URLError, OSError, ValueError, TypeError):
        raise TelegramUnavailable("telegram_api_unavailable") from None
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise ChannelNotVerified("channel_not_accessible")
    result = reply.get("result")
    if not isinstance(result, dict):
        raise TelegramUnavailable("invalid_telegram_response")
    return result


def verify_channel_admin(username: str, telegram_user_id: int) -> int:
    """Возвращает числовой chat_id только при подтверждённых правах."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or token.startswith("REPLACE_") or ":" not in token:
        raise TelegramUnavailable("telegram_token_unconfigured")
    bot = _telegram_call(token, "getMe", {})
    if bot.get("username", "").lower() != "mtproxy_pushok_bot" or bot.get("is_bot") is not True:
        raise TelegramUnavailable("unexpected_telegram_bot")
    bot_id = bot.get("id")
    if type(bot_id) is not int or bot_id <= 0:
        raise TelegramUnavailable("invalid_bot_id")

    chat = _telegram_call(token, "getChat", {"chat_id": "@" + username})
    chat_id = chat.get("id")
    if (
        chat.get("type") != "channel"
        or str(chat.get("username", "")).lower() != username.lower()
        or type(chat_id) is not int
        or chat_id >= 0
    ):
        raise ChannelNotVerified("not_a_matching_public_channel")

    bot_member = _telegram_call(
        token, "getChatMember", {"chat_id": chat_id, "user_id": bot_id}
    )
    if bot_member.get("status") not in ("creator", "administrator"):
        raise ChannelNotVerified("bot_not_channel_admin")

    user_member = _telegram_call(
        token, "getChatMember", {"chat_id": chat_id, "user_id": telegram_user_id}
    )
    if user_member.get("status") not in ("creator", "administrator"):
        raise ChannelNotVerified("user_not_channel_admin")
    return chat_id
