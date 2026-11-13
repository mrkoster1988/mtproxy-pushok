"""Очередь и решения оператора в Telegram: только личный чат.

Права всегда проверяются на сервере по актуальной роли, не по тексту
команды, username, callback_data или памяти polling-процесса.
"""
import logging
import time
from urllib.error import HTTPError, URLError
from uuid import UUID

LOG = logging.getLogger("pushok.bot.moderation")
_TTL = 600


def _reply(chat_id, text, callback_id=None, buttons=None):
    return ({
        "chat_id": chat_id,
        "text": text[:3900],
        "reply_markup": {"inline_keyboard": buttons or [
            [{"text": "📋 Очередь модерации", "callback_data": "moderation"}]
        ]},
    }, callback_id)


class OperatorInbox:
    def __init__(self, queue_loader, decider, unsubscribe, clock=None, miniapp_loader=None):
        self.queue_loader = queue_loader
        self.decider = decider
        self.unsubscribe = unsubscribe
        self.clock = clock or time.monotonic
        self.awaiting_reason = {}
        self.miniapp_loader = miniapp_loader

    @staticmethod
    def _failure(exception):
        if isinstance(exception, HTTPError) and exception.code == 403:
            return "🔒 Меню доступно только оператору или администратору MTProxy Pushok."
        if isinstance(exception, HTTPError) and exception.code == 409:
            return "Заявка уже обработана. Обновите очередь."
        return "Не удалось связаться с кабинетом. Повторите позже."

    def handle(self, update):
        callback = update.get("callback_query")
        message = update.get("message")
        item = callback or message or {}
        chat = ((callback.get("message") or {}) if callback else item).get("chat") or {}
        actor = item.get("from") or {}
        chat_id, user_id = chat.get("id"), actor.get("id")
        if (
            chat.get("type") != "private"
            or type(user_id) is not int or user_id <= 0
            or type(chat_id) is not int or chat_id != user_id
        ):
            return None
        callback_id = callback.get("id") if callback else None
        action = callback.get("data") if callback else None
        text = message.get("text") if message else None
        cmd = text.split(maxsplit=1)[0].lower() if isinstance(text, str) and text.strip() else ""
        now = self.clock()
        for uid, pending in list(self.awaiting_reason.items()):
            if pending["expires"] < now:
                self.awaiting_reason.pop(uid, None)

        if cmd in {"/moderation", "/moderation@mtproxy_pushok_bot"} or action == "moderation":
            self.awaiting_reason.pop(user_id, None)
            try:
                queue = self.queue_loader(user_id, subscribe=True)
            except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
                LOG.warning("Не удалось получить очередь: %s", type(exc).__name__)
                return _reply(chat_id, self._failure(exc), callback_id)
            if not queue.get("pending"):
                return _reply(
                    chat_id, "📋 Новых заявок на модерации нет.\n"
                             "🔔 Уведомления о новых заявках включены.\n"
                             "Выключить: /moderation_off",
                    callback_id,
                )
            lines = ["📋 Очередь модерации (до 5 заявок):\n",
                     "🔔 Уведомления включены. Выключить: /moderation_off"]
            buttons = []
            miniapp = self.miniapp_loader() if self.miniapp_loader else None
            for index, row in enumerate(queue["pending"][:5], start=1):
                campaign_id = UUID(str(row["campaign_id"]))
                title = " ".join(str(row["channel_title"]).split())[:64]
                price = row.get("price_kopecks")
                price_text = f"{price // 100} ₽" if type(price) is int else "—"
                lines.append(f"\n{index}. {title} — {price_text}\n"
                             f"{str(row['channel_url'])[:160]}")
                if miniapp:
                    buttons.append([{'text': f'📋 {index}. Разобрать заявку целиком',
                                     'web_app': {'url': miniapp + '#/moderation/' + str(campaign_id)}}])
                    continue
                buttons.append([
                    {"text": f"✅ {index}", "callback_data": f"review:approve:{campaign_id}"},
                    {"text": f"❌ {index}", "callback_data": f"review:reject:{campaign_id}"},
                    {"text": f"✏️ {index}", "callback_data": f"review:changes:{campaign_id}"},
                ])
            buttons.append([{"text": "🔄 Обновить", "callback_data": "moderation"}])
            return _reply(chat_id, "\n".join(lines), callback_id, buttons)

        if cmd in {"/moderation_off", "/moderation_off@mtproxy_pushok_bot"}:
            self.awaiting_reason.pop(user_id, None)
            try:
                self.unsubscribe(user_id)
            except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
                LOG.warning("Ошибка отключения уведомлений: %s", type(exc).__name__)
                return _reply(chat_id, self._failure(exc))
            return _reply(chat_id, "🔕 Уведомления модератора отключены.")

        if action == "review:cancel":
            self.awaiting_reason.pop(user_id, None)
            return _reply(chat_id, "Действие отменено.", callback_id)

        if isinstance(action, str) and action.startswith("review:"):
            parts = action.split(":")
            if len(parts) != 3 or parts[1] not in {"approve", "confirm", "reject", "changes"}:
                return _reply(chat_id, "Некорректная кнопка модерации.", callback_id)
            try:
                campaign_id = UUID(parts[2])
            except (ValueError, TypeError):
                return _reply(chat_id, "Некорректный номер заявки.", callback_id)
            verb = parts[1]
            if verb == "approve":
                return _reply(
                    chat_id, "Подтвердите одобрение заявки.\n"
                             "После этого статус оплаты станет «Ожидает оплаты».",
                    callback_id, [
                        [{"text": "✅ Подтвердить одобрение",
                          "callback_data": f"review:confirm:{campaign_id}"}],
                        [{"text": "Отмена", "callback_data": "review:cancel"}],
                    ],
                )
            if verb in {"reject", "changes"}:
                self.awaiting_reason[user_id] = {
                    "campaign_id": campaign_id,
                    "decision": "rejected" if verb == "reject" else "changes_requested",
                    "expires": now + _TTL,
                }
                return _reply(
                    chat_id,
                    "Напишите причину отказа." if verb == "reject"
                    else "Напишите рекламодателю, что необходимо исправить.",
                    callback_id,
                    [[{"text": "Отмена", "callback_data": "review:cancel"}]],
                )
            try:
                result = self.decider(user_id, campaign_id, "approved", None)
                if result.get("moderation_status") != "approved":
                    raise ValueError("Неверный результат модерации")
            except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
                LOG.warning("Ошибка одобрения: %s", type(exc).__name__)
                return _reply(chat_id, self._failure(exc), callback_id)
            return _reply(chat_id, "✅ Заявка одобрена. Следующий этап — проверка оплаты.",
                          callback_id)

        pending = self.awaiting_reason.get(user_id)
        if not pending or not isinstance(text, str) or text.startswith("/"):
            return None
        reason = text.strip()
        if not 3 <= len(reason) <= 2000:
            return _reply(chat_id, "Причина должна содержать от 3 до 2000 символов.")
        try:
            result = self.decider(user_id, pending["campaign_id"],
                                  pending["decision"], reason)
            if result.get("moderation_status") != pending["decision"]:
                raise ValueError("Неверный результат модерации")
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            LOG.warning("Ошибка решения модерации: %s", type(exc).__name__)
            return _reply(chat_id, self._failure(exc))
        self.awaiting_reason.pop(user_id, None)
        return _reply(chat_id, "✅ Решение по заявке сохранено. Рекламодатель увидит причину в кабинете.")
