"""Небольшой мастер черновика рекламы для личного чата Telegram.

Состояние живёт в памяти одного polling-процесса, максимум 512 диалогов по
10 минут. Подтверждение защищено устойчивым UUID в PostgreSQL.
"""
import logging
import re
import time
from urllib.error import HTTPError, URLError
from uuid import uuid4

LOG = logging.getLogger("pushok.bot.wizard")
_USERNAME = re.compile(
    r"^(?:@|https://(?:t\.me|telegram\.me)/|t\.me/)?"
    r"([A-Za-z][A-Za-z0-9_]{4,31})/?$", re.IGNORECASE
)
_RESERVED = {"joinchat", "addstickers", "share", "proxy", "socks", "login"}
_TTL_SECONDS = 600
_MAX_FLOWS = 512


def extract_channel_username(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    match = _USERNAME.fullmatch(value.strip())
    if not match:
        return None
    username = match.group(1).lower()
    return None if username in _RESERVED else username


def _answer(chat_id, text, callback_id=None, buttons=None):
    keyboard = buttons or [
        [{"text": "❌ Отмена", "callback_data": "draft:cancel"}]
    ]
    return ({"chat_id": chat_id, "text": text,
             "reply_markup": {"inline_keyboard": keyboard}}, callback_id)


class DraftWizard:
    def __init__(self, creator, enabled=True, clock=None):
        self.creator = creator
        self.enabled = enabled
        self.clock = clock or time.monotonic
        self.sessions = {}

    def _clean(self):
        now = self.clock()
        for telegram_id, state in list(self.sessions.items()):
            if state["expires"] < now:
                self.sessions.pop(telegram_id, None)
        return now

    def handle(self, update):
        callback = update.get("callback_query")
        message = update.get("message")
        item = callback or message or {}
        actor = item.get("from") or {}
        chat = (item.get("message") if callback else item) or {}
        chat = chat.get("chat") or {}
        telegram_id = actor.get("id")
        chat_id = chat.get("id")
        if (
            chat.get("type") != "private"
            or type(telegram_id) is not int
            or telegram_id <= 0
            or type(chat_id) is not int
            or chat_id != telegram_id
        ):
            return None

        now = self._clean()
        callback_id = callback.get("id") if callback else None
        action = callback.get("data") if callback else None
        text = message.get("text") if message else None
        first_word = text.split(maxsplit=1)[0].lower() if isinstance(text, str) and text.strip() else ""
        start = action == "advertise" or first_word in {"/advertise", "/advertise@mtproxy_pushok_bot"}
        if start:
            if not self.enabled:
                return _answer(chat_id, "Оформление через бота пока недоступно. Напишите в наш канал: https://t.me/pushok_proxy", callback_id)
            if telegram_id not in self.sessions and len(self.sessions) >= _MAX_FLOWS:
                return _answer(chat_id, "Сейчас много черновиков. Попробуйте позднее.", callback_id)
            self.sessions[telegram_id] = {"phase": "channel", "expires": now + _TTL_SECONDS}
            return _answer(chat_id,
                           "📢 Новое размещение MTProxy Pushok\n\n"
                           "Шаг 1 из 2. Отправьте @username своего публичного канала "
                           "или ссылку https://t.me/username.\n\n"
                           "Стоимость: 1 день — 300 ₽. Оплата пока не принимается.",
                           callback_id)

        if action == "draft:cancel" or first_word == "/cancel":
            self.sessions.pop(telegram_id, None)
            return _answer(chat_id, "Создание заявки отменено.", callback_id,
                           [[{"text": "🏠 Главное меню", "callback_data": "start"}]])

        state = self.sessions.get(telegram_id)
        if action == "draft:confirm":
            if not state or state["phase"] != "confirm":
                return _answer(chat_id, "Черновик больше не ожидает подтверждения. Начните с /advertise.", callback_id)
            try:
                result = self.creator(telegram_id, state["username"], state["request_key"])
            except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
                LOG.warning("Ошибка создания черновика: %s", type(exc).__name__)
                return _answer(chat_id,
                               "Не получилось сохранить черновик. Попробуйте нажать «Создать черновик» ещё раз.",
                               callback_id,
                               [[{"text": "✅ Создать черновик", "callback_data": "draft:confirm"}],
                                [{"text": "❌ Отмена", "callback_data": "draft:cancel"}]])
            self.sessions.pop(telegram_id, None)
            if not isinstance(result, dict) or not result.get("campaign_id"):
                return _answer(chat_id, "API ответил некорректно. Проверьте «Мои кампании».", callback_id)
            return _answer(chat_id,
                           "✅ Черновик создан для @" + state["username"] + ".\n"
                           "Тариф: 1 день — 300 ₽.\n"
                           "Заявка пока НЕ отправлена на модерацию и НЕ оплачена.\n"
                           "Посмотрите её в разделе «Мои кампании».",
                           callback_id,
                           [[{"text": "🔍 Проверить канал и отправить на модерацию",
                              "callback_data": "submit:" + str(result["campaign_id"])}],
                            [{"text": "📋 Мои кампании", "callback_data": "campaigns"}]])
        if action and action.startswith("draft:"):
            return None

        if not state or not isinstance(text, str) or text.startswith("/"):
            return None
        if state["phase"] != "channel":
            return _answer(chat_id, "Подтвердите создание черновика или отмените заявку.")
        username = extract_channel_username(text)
        if not username:
            return _answer(chat_id,
                           "Не удалось распознать публичный канал.\n"
                           "Отправьте, например, @examplechannel или https://t.me/examplechannel.")
        state.update({"phase": "confirm", "username": username,
                      "request_key": str(uuid4()), "expires": now + _TTL_SECONDS})
        return _answer(chat_id,
                       "Шаг 2 из 2. Подтвердите:\n"
                       "Канал: @" + username + "\n"
                       "Формат: MTProxy Sponsor\n"
                       "Тариф: 1 день — 300 ₽\n\n"
                       "Сейчас создадим только черновик, без оплаты и публикации.",
                       None,
                       [[{"text": "✅ Создать черновик", "callback_data": "draft:confirm"}],
                        [{"text": "❌ Отмена", "callback_data": "draft:cancel"}]])
