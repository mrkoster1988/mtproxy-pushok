"""Минимальный Telegram-бот MTProxy Pushok. Python standard library."""
import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CHANNEL_URL = "https://t.me/pushok_proxy"
BOT_USERNAME = "mtproxy_pushok_bot"
COMMANDS = frozenset(("start", "advertise", "campaigns", "tariffs", "channels", "help"))
LOG = logging.getLogger("pushok.bot")


def keyboard():
    return {"inline_keyboard": [
        [{"text": "📢 Разместить рекламу", "callback_data": "advertise"}],
        [{"text": "📋 Мои кампании", "callback_data": "campaigns"},
         {"text": "💎 Тарифы", "callback_data": "tariffs"}],
        [{"text": "📣 Наш канал", "url": CHANNEL_URL}],
        [{"text": "❓ Помощь", "callback_data": "help"}],
    ]}


def message_for(command):
    if command == "start":
        return ("🐱 Добро пожаловать в MTProxy Pushok!\n\n"
                "Выберите действие в меню.\n\n"
                "⚙️ Автоматическое размещение пока разрабатывается.")
    if command == "advertise":
        return ("📢 Пока заявки принимаются через сообщения нашему каналу:\n"
                + CHANNEL_URL + "\nОформление через бота в разработке.")
    if command == "campaigns":
        return "📋 Просмотр личных кампаний через бота ещё не подключён."
    if command == "tariffs":
        return ("💎 Базовый тариф: 1 день — 300 ₽.\n"
                "Продажа и запуск прокси-размещений пока недоступны.")
    if command == "channels":
        return "📣 Официальный канал:\n" + CHANNEL_URL
    return "❓ Поддержка: напишите через сообщения каналу\n" + CHANNEL_URL


def response_for(update):
    """Вернуть (sendMessage_payload, callback_query_id) либо None."""
    callback = update.get("callback_query")
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        command = callback.get("data")
        if chat.get("type") != "private" or command not in COMMANDS:
            return None
        if not isinstance(chat.get("id"), int):
            return None
        return ({"chat_id": chat["id"], "text": message_for(command),
                 "reply_markup": keyboard()}, callback.get("id"))
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    content = message.get("text")
    if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
        return None
    if not isinstance(content, str) or not content.startswith("/"):
        return None
    parts = content.split(maxsplit=1)[0][1:].split("@", 1)
    if len(parts) == 2 and parts[1].lower() != BOT_USERNAME:
        return None
    command = parts[0].lower()
    if command not in COMMANDS:
        return None
    return ({"chat_id": chat["id"], "text": message_for(command),
             "reply_markup": keyboard()}, None)


def telegram_call(token, method, payload):
    req = Request("https://api.telegram.org/bot" + token + "/" + method,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=40) as stream:
        result = json.load(stream)
    if not result.get("ok"):
        raise RuntimeError("Telegram API отклонил запрос")
    return result["result"]


def run():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token.startswith("REPLACE_"):
        raise SystemExit("Требуется TELEGRAM_BOT_TOKEN в окружении.")
    logging.basicConfig(level=logging.INFO)
    offset = None
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {
                "offset": offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query"]})
            for update in updates:
                offset = update["update_id"] + 1
                response = response_for(update)
                if response is None:
                    continue
                payload, callback_id = response
                if callback_id:
                    telegram_call(token, "answerCallbackQuery",
                                  {"callback_query_id": callback_id})
                telegram_call(token, "sendMessage", payload)
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Не включать str(exc): HTTPError содержит URL с секретным токеном.
            LOG.warning("Ошибка Telegram API: %s, повтор через 5 сек.", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    run()
