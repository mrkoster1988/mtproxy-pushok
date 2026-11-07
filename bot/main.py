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


def fetch_campaigns(telegram_user_id):
    """Read-only запрос к внутреннему API по подтверждённому Telegram ID."""
    secret = os.getenv("BOT_INTERNAL_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Интеграция с кабинетом не настроена")
    body = json.dumps({"telegram_user_id": telegram_user_id}).encode("utf-8")
    request = Request(
        "http://api:8000/api/v1/internal/bot/campaigns",
        data=body,
        headers={"Content-Type": "application/json", "X-Pushok-Bot-Secret": secret},
        method="POST",
    )
    with urlopen(request, timeout=5) as stream:
        result = json.load(stream)
    if not isinstance(result, list):
        raise ValueError("Неверный формат ответа API")
    return result


def format_campaigns(campaigns):
    if not campaigns:
        return ("📋 Пока у вас нет заявок в рекламном кабинете.\\n"
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
            f"\\n{index}. {title}\\n"
            f"Тариф: {campaign.get('tariff_name_snapshot', '—')} · {price_text}\\n"
            f"Модерация: {moderation.get(campaign.get('moderation_status'), '—')}\\n"
            f"Оплата: {payment.get(campaign.get('payment_status'), '—')}\\n"
            f"Размещение: {placement.get(campaign.get('placement_status'), '—')}"
        )
    lines.append("\\nПоказаны до 10 последних заявок. Подробности доступны в кабинете.")
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


def response_for(update, campaign_loader=None):
    """Вернуть (sendMessage_payload, callback_query_id) либо None."""
    callback = update.get("callback_query")
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


def telegram_call(token, method, payload):
    req = Request("https://api.telegram.org/bot" + token + "/" + method,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=40) as stream:
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
    offset = None
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {
                "offset": offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query"]})
            for update in updates:
                next_offset = update["update_id"] + 1
                response = response_for(update, campaign_loader=fetch_campaigns)
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
