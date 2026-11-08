"""Приватное меню диагностики уведомлений для администратора Telegram-бота.

Проверка полномочий выполняется API на каждом запросе через Telegram ID
и актуальную роль в PostgreSQL. Callback не является авторизацией.
"""
import logging
from urllib.error import HTTPError, URLError
from uuid import UUID

LOG = logging.getLogger("pushok.bot.delivery")


def _reply(chat_id, message, callback_id=None, buttons=None):
    return ({
        "chat_id": chat_id,
        "text": message[:3900],
        "reply_markup": {"inline_keyboard": buttons or [
            [{"text": "🔄 Обновить диагностику", "callback_data": "delivery:refresh"}]
        ]},
    }, callback_id)


class DeliveryInbox:
    def __init__(self, report_loader, retry):
        self.report_loader = report_loader
        self.retry = retry

    def handle(self, update):
        callback = update.get("callback_query")
        message = update.get("message")
        item = callback or message or {}
        actor = item.get("from") or {}
        chat = ((callback.get("message") or {}) if callback else item).get("chat") or {}
        uid, chat_id = actor.get("id"), chat.get("id")
        if (
            chat.get("type") != "private"
            or type(uid) is not int or uid <= 0
            or type(chat_id) is not int or chat_id != uid
        ):
            return None

        action = callback.get("data") if callback else None
        callback_id = callback.get("id") if callback else None
        text = message.get("text") if message else None
        command = text.split(maxsplit=1)[0].lower() if isinstance(text, str) and text.strip() else ""
        show_report = (
            command in ("/delivery", "/delivery@mtproxy_pushok_bot")
            or action == "delivery:refresh"
        )
        if isinstance(action, str) and action.startswith("delivery:retry:"):
            try:
                notice_id = UUID(action[len("delivery:retry:"):])
            except (ValueError, TypeError):
                return _reply(chat_id, "Некорректный идентификатор уведомления.", callback_id)
            try:
                outcome = self.retry(uid, notice_id)
                if outcome.get("queued") is not True:
                    raise ValueError("API не подтвердил повторную попытку")
            except HTTPError as exc:
                if exc.code == 403:
                    msg = "🔒 Диагностика доступна только администратору MTProxy Pushok."
                elif exc.code == 409:
                    msg = ("Повтор сейчас недоступен: сообщение уже доставлено, "
                           "заблокировано или отправляется.")
                else:
                    msg = "Не удалось запланировать повтор. Проверьте позже."
                LOG.warning("Ошибка ручного повтора: HTTP %s", exc.code)
                return _reply(chat_id, msg, callback_id)
            except (URLError, OSError, RuntimeError, ValueError) as exc:
                LOG.warning("Ошибка ручного повтора: %s", type(exc).__name__)
                return _reply(chat_id, "Не удалось запланировать повтор. Проверьте позже.", callback_id)
            return _reply(
                chat_id, "✅ Повторная попытка поставлена в очередь. "
                         "Это не подтверждение доставки в Telegram.", callback_id
            )
        if not show_report:
            return None
        try:
            report = self.report_loader(uid)
            advertiser = report["advertiser"]
            operator = report["operator"]
            recent = report["recent_undelivered"][:8]
        except HTTPError as exc:
            LOG.warning("Отказ в диагностике: HTTP %s", exc.code)
            return _reply(
                chat_id,
                "🔒 Только администратор MTProxy Pushok." if exc.code == 403
                else "Сервис диагностики временно недоступен.",
                callback_id,
            )
        except (URLError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            LOG.warning("Диагностика недоступна: %s", type(exc).__name__)
            return _reply(chat_id, "Сервис диагностики временно недоступен.", callback_id)

        def summarize(label, counts):
            return (
                f"{label}: ожидают {int(counts['waiting'])}, "
                f"в обработке {int(counts['in_flight'])}, "
                f"доставлено {int(counts['delivered'])}, "
                f"не доставить {int(counts.get('abandoned', 0))}"
            )
        lines = [
            "📨 Диагностика уведомлений MTProxy Pushok",
            summarize("Рекламодатели", advertiser),
            summarize("Операторы", operator),
            "",
            "Последние недоставленные сообщения рекламодателям:",
        ]
        buttons = []
        for index, notice in enumerate(recent, 1):
            notice_id = UUID(str(notice["notice_id"]))
            campaign_id = UUID(str(notice["campaign_id"]))
            status = notice["status"]
            label = {
                "waiting": "ожидает",
                "in_flight": "отправляется",
                "abandoned": "постоянный отказ",
            }.get(status, "неизвестен")
            attempts = int(notice["attempts"])
            lines.append(
                f"{index}. Заявка {str(campaign_id)[:8]} · {label} · попыток {attempts}"
            )
            if status == "waiting" and attempts > 0:
                buttons.append([{
                    "text": f"🔁 Повторить №{index}",
                    "callback_data": f"delivery:retry:{notice_id}",
                }])
        if not recent:
            lines.append("Нет.")
        lines.append(
            "\nПовтор доступен только для временных сбоев. Telegram 403 "
            "не сбрасывается автоматически."
        )
        buttons.append([{"text": "🔄 Обновить", "callback_data": "delivery:refresh"}])
        return _reply(chat_id, "\n".join(lines), callback_id, buttons)
