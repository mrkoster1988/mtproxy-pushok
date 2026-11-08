"""Операторские кнопки MTProxy Pushok: очередь, решение, комментарий, история.

Никакие права не назначаются локально: API заново проверяет operator/admin
для каждого запроса. В памяти только короткий диалог ввода комментария.
"""
import logging
import time
from urllib.error import HTTPError, URLError
from uuid import UUID

LOG = logging.getLogger("pushok.bot.operator")
MAX_SESSIONS = 128
TTL_SECONDS = 600


def _reply(chat_id, text, callback_id=None, keyboard=None):
    result = {"chat_id": chat_id, "text": text[:3900]}
    if keyboard:
        result["reply_markup"] = {"inline_keyboard": keyboard}
    return (result, callback_id)


def operator_keyboard(campaign_id):
    return [
        [{"text": "✅ Одобрить", "callback_data": "op:a:" + str(campaign_id)}],
        [{"text": "📝 На доработку", "callback_data": "op:c:" + str(campaign_id)},
         {"text": "⛔ Отклонить", "callback_data": "op:r:" + str(campaign_id)}],
    ]


def notification_payload(item, operator_id):
    """Сообщение администратору; ack выполняется только после sendMessage."""
    title = " ".join(str(item["channel_title"]).split())[:75]
    cid = str(UUID(str(item["id"])))
    text = (
        "🐱 Новая заявка MTProxy Pushok\n"
        f"Канал: {title}\n"
        f"Ссылка: {item['channel_url']}\n"
        f"Стоимость: {item['price_kopecks_snapshot'] / 100:g} ₽\n"
        "Статус: На модерации\n"
        "Проверьте заявку перед принятием решения."
    )
    return {
        "chat_id": operator_id,
        "text": text,
        "reply_markup": {"inline_keyboard": operator_keyboard(cid)},
    }


def queue_payload(telegram_id, loader):
    items = loader(telegram_id)
    if not isinstance(items, list):
        raise ValueError("Неверный ответ очереди")
    if not items:
        return _reply(
            telegram_id, "🐱 Сейчас нет заявок на модерации.",
            keyboard=[[{"text": "🔄 Обновить", "callback_data": "op:q"},
                       {"text": "📜 История", "callback_data": "op:h"}]],
        )[0]
    lines = ["🐱 Очередь рекламных заявок:"]
    keyboard = []
    for n, item in enumerate(items[:5], 1):
        cid = str(UUID(str(item["id"])))
        title = " ".join(str(item["channel_title"]).split())[:65]
        price = item.get("price_kopecks_snapshot", 0)
        lines.append(
            f"\n{n}. {title}\n{item['channel_url']}\n"
            f"Тариф: {price / 100:g} ₽ / 1 день"
        )
        keyboard.extend(operator_keyboard(cid))
    keyboard.append([{"text": "🔄 Обновить", "callback_data": "op:q"},
                     {"text": "📜 История", "callback_data": "op:h"}])
    return {"chat_id": telegram_id, "text": "\n".join(lines)[:3900],
            "reply_markup": {"inline_keyboard": keyboard}}


def history_payload(telegram_id, loader):
    events = loader(telegram_id)
    if not isinstance(events, list):
        raise ValueError("Неверный формат истории")
    if not events:
        return {"chat_id": telegram_id, "text": "📜 Вы ещё не принимали решений по заявкам."}
    names = {"approved": "Одобрено", "changes_requested": "На доработку", "rejected": "Отклонено"}
    lines = ["📜 Ваши последние решения по заявкам:"]
    for n, event in enumerate(events[:10], 1):
        title = " ".join(str(event["channel_title"]).split())[:55]
        lines.append(f"{n}. {title}: {names.get(event['decision'], 'Решение')}")
    return {"chat_id": telegram_id, "text": "\n".join(lines)[:3900]}


class OperatorMenu:
    def __init__(self, queue_loader, reviewer, history_loader, clock=None):
        self.queue_loader = queue_loader
        self.reviewer = reviewer
        self.history_loader = history_loader
        self.clock = clock or time.monotonic
        self.sessions = {}

    def _clean(self):
        now = self.clock()
        for user_id, state in list(self.sessions.items()):
            if state["expires"] < now:
                self.sessions.pop(user_id, None)
        return now

    def handle(self, update):
        callback = update.get("callback_query")
        message = update.get("message")
        item = callback or message or {}
        chat = (item.get("message") if callback else item) or {}
        chat = chat.get("chat") or {}
        sender = item.get("from") or {}
        uid, cid = sender.get("id"), chat.get("id")
        if chat.get("type") != "private" or type(uid) is not int or uid <= 0 or cid != uid:
            return None

        cb = callback.get("data") if callback else None
        text = message.get("text") if message else None
        word = text.split(maxsplit=1)[0].lower() if isinstance(text, str) and text.strip() else ""
        now = self._clean()
        callback_id = callback.get("id") if callback else None
        if cb not in ("op:q", "op:h") and not (isinstance(cb, str) and cb.startswith("op:")) and word not in (
            "/moderation", "/moderation@mtproxy_pushok_bot",
            "/history", "/history@mtproxy_pushok_bot",
            "/cancel", "/cancel@mtproxy_pushok_bot",
        ) and uid not in self.sessions:
            return None

        if word in ("/cancel", "/cancel@mtproxy_pushok_bot") and uid in self.sessions:
            self.sessions.pop(uid, None)
            return _reply(uid, "Ввод комментария отменён.")
        if word in ("/moderation", "/moderation@mtproxy_pushok_bot") or cb == "op:q":
            self.sessions.pop(uid, None)
            try:
                result = queue_payload(uid, self.queue_loader)
            except HTTPError as exc:
                result = {"chat_id": uid, "text":
                          "⛔ Нет доступа к очереди модерации." if exc.code == 403
                          else "Очередь временно недоступна."}
            except (URLError, OSError, RuntimeError, ValueError, KeyError, TypeError):
                LOG.warning("Не удалось получить очередь оператора")
                result = {"chat_id": uid, "text": "Очередь временно недоступна."}
            return (result, callback_id)
        if word in ("/history", "/history@mtproxy_pushok_bot") or cb == "op:h":
            try:
                result = history_payload(uid, self.history_loader)
            except HTTPError as exc:
                result = {"chat_id": uid, "text":
                          "⛔ Нет доступа к истории модерации." if exc.code == 403
                          else "История временно недоступна."}
            except (URLError, OSError, RuntimeError, ValueError, KeyError, TypeError):
                LOG.warning("Не удалось получить историю модерации")
                result = {"chat_id": uid, "text": "История временно недоступна."}
            return (result, callback_id)

        if isinstance(cb, str) and cb.startswith("op:"):
            parts = cb.split(":")
            if len(parts) != 3 or parts[1] not in ("a", "c", "r"):
                return _reply(uid, "Кнопка устарела, откройте /moderation.", callback_id)
            try:
                campaign_id = UUID(parts[2])
            except (ValueError, TypeError):
                return _reply(uid, "Неверный ID заявки. Откройте /moderation.", callback_id)
            if parts[1] == "a":
                return self._review(uid, campaign_id, "approved", None, callback_id)
            if uid not in self.sessions and len(self.sessions) >= MAX_SESSIONS:
                return _reply(uid, "Слишком много активных диалогов. Попробуйте позднее.", callback_id)
            decision = "changes_requested" if parts[1] == "c" else "rejected"
            self.sessions[uid] = {"campaign_id": campaign_id, "decision": decision,
                                  "expires": now + TTL_SECONDS}
            return _reply(uid, ("📝 Напишите причину возврата на доработку." if decision == "changes_requested"
                                else "⛔ Напишите причину отклонения.")
                          + "\nОт 1 до 2000 символов. Для отмены: /cancel.", callback_id)

        state = self.sessions.get(uid)
        if not state or not isinstance(text, str):
            return None
        if text.startswith("/"):
            return None
        comment = text.strip()
        if not (1 <= len(comment) <= 2000):
            return _reply(uid, "Комментарий должен содержать от 1 до 2000 символов.")
        result = self._review(uid, state["campaign_id"], state["decision"], comment)
        if result[0]["text"].startswith("✅"):
            self.sessions.pop(uid, None)
        return result

    def _review(self, uid, campaign_id, decision, comment, callback_id=None):
        try:
            result = self.reviewer(uid, campaign_id, decision, comment)
            if not isinstance(result, dict) or result.get("moderation_status") != decision:
                raise ValueError("API не подтвердил модерацию")
        except HTTPError as exc:
            message = {
                403: "⛔ У вас нет прав модератора.",
                409: "Заявка уже обработана или больше не ожидает модерации.",
                422: "Проверьте комментарий для решения.",
            }.get(exc.code, "Не удалось обработать заявку. Попробуйте позднее.")
            if exc.code in (403, 409):
                self.sessions.pop(uid, None)
            LOG.warning("Ошибка модерации: HTTP %s", exc.code)
            return _reply(uid, message, callback_id)
        except (URLError, OSError, RuntimeError, ValueError):
            LOG.warning("Временная ошибка модерации")
            return _reply(uid, "Не удалось обработать заявку. Попробуйте позднее.", callback_id)
        labels = {"approved": "Одобрено — ожидает ручной оплаты",
                  "changes_requested": "Возвращено на доработку",
                  "rejected": "Отклонено"}
        return _reply(uid, "✅ Решение сохранено: " + labels[decision] +
                      "\nВключение рекламы и приём денег автоматически не запускаются.",
                      callback_id,
                      keyboard=[[{"text": "📋 Очередь", "callback_data": "op:q"}]])
