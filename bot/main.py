"""Минимальный Telegram-бот MTProxy Pushok. Python standard library."""
import json
import logging
import os
import time
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .wizard import DraftWizard
from .operator_inbox import OperatorInbox

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


def _internal_api(route, payload):
    secret = os.getenv("BOT_INTERNAL_SECRET", "")
    if len(secret) != 64 or any(c not in "0123456789abcdef" for c in secret):
        raise RuntimeError("Интеграция с кабинетом не настроена")
    request = Request(
        "http://api:8000/api/v1/internal/bot/" + route,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pushok-Bot-Secret": secret},
        method="POST",
    )
    with urlopen(request, timeout=5) as stream:
        return json.load(stream)


def fetch_campaigns(telegram_user_id):
    result = _internal_api("campaigns", {"telegram_user_id": telegram_user_id})
    if not isinstance(result, list):
        raise ValueError("Неверный формат ответа API")
    return result


def create_campaign_draft(telegram_user_id, channel_username, request_key):
    result = _internal_api("campaign-drafts", {
        "telegram_user_id": telegram_user_id,
        "channel_username": channel_username,
        "request_key": request_key,
    })
    if not isinstance(result, dict) or not result.get("campaign_id"):
        raise ValueError("Неверный ответ о сохранении черновика")
    return result


def submit_campaign_draft(telegram_user_id, campaign_id):
    result = _internal_api("campaign-drafts/submit", {
        "telegram_user_id": telegram_user_id,
        "campaign_id": str(campaign_id),
    })
    if not isinstance(result, dict) or result.get("moderation_status") != "pending":
        raise ValueError("API не подтвердил отправку на модерацию")
    if result.get("verified_now") is True:
        # Уведомление опционально: нельзя отменять уже сохранённую заявку.
        notify_subscribed_operators(campaign_id)
    return result


def operator_queue(user_id, subscribe=True):
    result = _internal_api("operators/queue", {
        "telegram_user_id": user_id, "subscribe": subscribe
    })
    if not isinstance(result, dict) or not isinstance(result.get("pending"), list):
        raise ValueError("Неправильный формат очереди")
    return result


def operator_unsubscribe(user_id):
    return _internal_api("operators/unsubscribe", {"telegram_user_id": user_id})


def operator_decide(user_id, campaign_id, decision, comment=None):
    result = _internal_api("operators/decide", {
        "telegram_user_id": user_id,
        "campaign_id": str(campaign_id),
        "decision": decision,
        "comment": comment,
    })
    if not isinstance(result, dict) or result.get("moderation_status") != decision:
        raise ValueError("Модерация не подтверждена")
    return result


def notify_subscribed_operators(campaign_id):
    """Лучшее усилие, не изменяющее статус кампании при отказе Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        user_ids = _internal_api("operators/recipients", {})
        if not isinstance(user_ids, list):
            raise ValueError("Неверный список подписчиков")
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Не удалось прочитать подписчиков: %s", type(exc).__name__)
        return
    content = (
        "📨 Новая заявка на модерацию MTProxy Pushok\n"
        f"Заявка: {str(campaign_id)[:8]}\n"
        "Тариф: 1 день — 300 ₽\n"
        "Откройте /moderation для проверки."
    )
    for telegram_id in user_ids[:5]:
        if type(telegram_id) is not int or telegram_id <= 0:
            continue
        try:
            telegram_call(token, "sendMessage", {
                "chat_id": telegram_id,
                "text": content,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "📋 Открыть очередь", "callback_data": "moderation"},
                ]]},
            }, timeout=4)
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Администратор мог ещё не открыть диалог с ботом или заблокировать его.
            # Не раскрываем токен или URL Telegram Bot API.
            LOG.warning("Уведомление Telegram не доставлено: %s", type(exc).__name__)


def submission_reply(callback, submitter):
    chat = (callback.get("message") or {}).get("chat") or {}
    actor = callback.get("from") or {}
    chat_id = chat.get("id")
    telegram_id = actor.get("id")
    if (
        chat.get("type") != "private" or type(telegram_id) is not int
        or telegram_id <= 0 or type(chat_id) is not int or chat_id != telegram_id
    ):
        return None
    callback_id = callback.get("id")
    raw = callback.get("data", "")[7:]
    try:
        campaign_id = UUID(raw)
    except (ValueError, TypeError, AttributeError):
        return ({"chat_id": chat_id, "text": "Не удалось определить заявку. Начните с /advertise."}, callback_id)
    if submitter is None:
        return ({"chat_id": chat_id, "text": "Проверка канала пока недоступна."}, callback_id)
    try:
        result = submitter(telegram_id, campaign_id)
        if isinstance(result, dict) and result.get("verified_now") is True:
            text = ("✅ Права администратора канала подтверждены.\n"
                    "Черновик отправлен на модерацию.\n"
                    "Оплату и размещение рекламы пока не выполняем.")
        else:
            text = ("✅ Заявка уже находится на модерации.\n"
                    "Повторная проверка прав администратора не выполнялась.")
    except HTTPError as exc:
        if exc.code == 409:
            text = ("⚠️ Не удалось подтвердить права на канал.\n"
                    "Добавьте @mtproxy_pushok_bot администратором рекламируемого "
                    "канала и убедитесь, что вы сами его администратор.\n"
                    "После этого нажмите кнопку проверки повторно.")
        elif exc.code == 404:
            text = "Заявка не найдена в вашем кабинете."
        else:
            text = "Проверка Telegram временно недоступна. Попробуйте позже."
        LOG.warning("Ошибка проверки канала: HTTP %s", exc.code)
    except (URLError, OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Ошибка проверки канала: %s", type(exc).__name__)
        text = "Проверка Telegram временно недоступна. Попробуйте позже."
    return ({"chat_id": chat_id, "text": text,
             "reply_markup": {"inline_keyboard": [
                 [{"text": "📋 Мои кампании", "callback_data": "campaigns"}]
             ]}}, callback_id)


def format_campaigns(campaigns):
    if not campaigns:
        return ("📋 Пока у вас нет заявок в рекламном кабинете.\n"
                "Оформление заказов через бота пока в разработке.")
    moderation = {"draft": "Черновик", "pending": "На модерации",
                  "approved": "Одобрено", "rejected": "Отклонено",
                  "changes_requested": "Нужны исправления"}
    payment = {"not_required": "Не требуется", "awaiting_payment": "Ожидается",
               "proof_submitted": "Чек на проверке", "verified": "Подтверждена",
               "rejected": "Отклонена", "refunded": "Возврат"}
    placement = {"not_scheduled": "Не назначено", "scheduled": "Запланировано",
                 "active": "Активно", "completed": "Завершено",
                 "cancelled": "Отменено"}
    lines = ["📋 Ваши последние рекламные заявки:"]
    for index, campaign in enumerate(campaigns[:10], 1):
        title = " ".join(str(campaign.get("channel_title") or "Без названия").split())[:64]
        price = campaign.get("price_kopecks_snapshot", 0)
        currency = (campaign.get("currency_snapshot") or "RUB").strip()
        price_text = (f"{price / 100:g} ₽" if currency == "RUB" and type(price) is int
                      else "Цена в кабинете")
        lines.append(
            f"\n{index}. {title}\n"
            f"Тариф: {campaign.get('tariff_name_snapshot', '—')} · {price_text}\n"
            f"Модерация: {moderation.get(campaign.get('moderation_status'), '—')}\n"
            f"Оплата: {payment.get(campaign.get('payment_status'), '—')}\n"
            f"Размещение: {placement.get(campaign.get('placement_status'), '—')}"
        )
    lines.append("\nПоказаны до 10 последних заявок. Подробности доступны в кабинете.")
    return "".join(lines)[:3900]


def command_text(command, actor, chat_id, campaign_loader=None):
    if command != "campaigns":
        return message_for(command)
    telegram_id = (actor or {}).get("id")
    if type(telegram_id) is not int or telegram_id <= 0 or telegram_id != chat_id:
        return "Откройте личный чат с ботом для просмотра ваших заявок."
    if campaign_loader is None:
        return message_for("campaigns")
    try:
        return format_campaigns(campaign_loader(telegram_id))
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Недоступен внутренний API: %s", type(exc).__name__)
        return "📋 Заявки временно недоступны. Попробуйте позже."


def response_for(update, campaign_loader=None, wizard=None, submitter=None,
                 operator_inbox=None):
    """Вернуть (sendMessage_payload, callback_query_id) либо None."""
    if operator_inbox is not None:
        operator_reply = operator_inbox.handle(update)
        if operator_reply is not None:
            return operator_reply
    if wizard is not None:
        draft_reply = wizard.handle(update)
        if draft_reply is not None:
            return draft_reply
    callback = update.get("callback_query")
    if callback and isinstance(callback.get("data"), str) and callback["data"].startswith("submit:"):
        return submission_reply(callback, submitter)
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        command = callback.get("data")
        if chat.get("type") != "private" or command not in COMMANDS:
            return None
        if not isinstance(chat.get("id"), int):
            return None
        return ({"chat_id": chat["id"], "text": command_text(
            command, callback.get("from"), chat["id"], campaign_loader),
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
    return ({"chat_id": chat["id"], "text": command_text(
        command, message.get("from"), chat["id"], campaign_loader),
             "reply_markup": keyboard()}, None)


def telegram_call(token, method, payload, timeout=40):
    req = Request("https://api.telegram.org/bot" + token + "/" + method,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as stream:
        result = json.load(stream)
    if not result.get("ok"):
        raise RuntimeError("Telegram API отклонил запрос")
    return result["result"]


def validate_bot(token):
    """Проверить правильность токена и отсутствие webhook перед polling."""
    info = telegram_call(token, "getMe", {})
    if info.get("username", "").lower() != BOT_USERNAME:
        raise RuntimeError("Токен принадлежит другому Telegram-боту")
    webhook = telegram_call(token, "getWebhookInfo", {})
    if webhook.get("url"):
        raise RuntimeError("Webhook включён: polling не запускается")


def run():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token.startswith("REPLACE_"):
        raise SystemExit("Требуется TELEGRAM_BOT_TOKEN в окружении.")
    logging.basicConfig(level=logging.INFO)
    try:
        validate_bot(token)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        LOG.error("Проверка бота не пройдена: %s", type(exc).__name__)
        raise SystemExit("Не удалось запустить бота: проверьте токен, username и webhook.") from None
    secret = os.getenv("BOT_INTERNAL_SECRET", "")
    ready = len(secret) == 64 and all(c in "0123456789abcdef" for c in secret)
    wizard = DraftWizard(create_campaign_draft, enabled=ready)
    operator_inbox = OperatorInbox(operator_queue, operator_decide, operator_unsubscribe)
    offset = None
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {
                "offset": offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query"]})
            for update in updates:
                next_offset = update["update_id"] + 1
                response = response_for(update, campaign_loader=fetch_campaigns, wizard=wizard,
                                        submitter=submit_campaign_draft,
                                        operator_inbox=operator_inbox)
                if response is not None:
                    payload, callback_id = response
                    if callback_id:
                        telegram_call(token, "answerCallbackQuery",
                                      {"callback_query_id": callback_id})
                    telegram_call(token, "sendMessage", payload)
                # Не подтверждать update до завершения обработки.
                offset = next_offset
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Не включать str(exc): HTTPError содержит URL с секретным токеном.
            LOG.warning("Ошибка Telegram API: %s, повтор через 5 сек.", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    run()
