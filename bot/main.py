"""Минимальный Telegram-бот MTProxy Pushok. Python standard library."""
import json
import logging
import os
import re
import time
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from .telegram_environment import bot_api_url, environment_name
from .wizard import DraftWizard, format_tariff
from .navigation import Navigation
from .advertiser_notices import deliver_advertiser_notices
from .operator_inbox import OperatorInbox
from .delivery_inbox import DeliveryInbox
from .button_icons import decorate, configured_icons, without_button_icons, icon_rejection
from .button_styles import with_button_styles

CHANNEL_URL = "https://t.me/pushok_proxy"
BOT_USERNAME = "mtproxy_pushok_bot"


def configured_bot_username():
    """Имя бота берётся из мастера установки, а не из чужого проекта."""
    username = os.getenv("TELEGRAM_BOT_USERNAME", BOT_USERNAME)
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise RuntimeError("Неверный TELEGRAM_BOT_USERNAME в настройках")
    return username.lower()


COMMANDS = frozenset(("start", "advertise", "campaigns", "tariffs", "channels", "help", "paysupport"))
LOG = logging.getLogger("pushok.bot")


def miniapp_url():
    if os.getenv('PUSHOK_MINIAPP_ENABLED', '0') != '1':
        return None
    raw = os.getenv('PUSHOK_MINIAPP_URL', '')
    try:
        url = urlsplit(raw)
        if (any(c.isspace() for c in raw) or url.scheme != 'https' or url.username or url.password
            or url.port not in (None, 443) or url.query or url.fragment
            or url.path not in ('/miniapp', '/miniapp/')
            or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', url.hostname or '')):
            return None
    except ValueError:
        return None
    return raw


def keyboard():
    result = {"inline_keyboard": [
        [{"text": "📢 Разместить рекламу", "callback_data": "advertise"}],
        [{"text": "📋 Мои кампании", "callback_data": "campaigns"},
         {"text": "💎 Тарифы", "callback_data": "tariffs"}],
        [{"text": "📣 Наш канал", "url": CHANNEL_URL}],
        [{"text": "❓ Помощь", "callback_data": "help"}],
    ]}
    url = miniapp_url()
    if url:
        result['inline_keyboard'].insert(0, [{'text': '🐱 Открыть Mini App', 'web_app': {'url': url}}])
    # Необязательные фирменные иконки; без реальных IDs структура кнопок не меняется.
    icons = configured_icons()
    for row in result['inline_keyboard']:
        for position, button in enumerate(row):
            action = button.get('callback_data')
            if action is None and button.get('url') == CHANNEL_URL:
                action = 'channels'
            if action is None and 'web_app' in button:
                action = 'miniapp'
            if action:
                row[position] = decorate(button, action, icons)
    return result


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
        return "💎 Актуальная стоимость и доступность оплаты указаны в кабинете."
    if command == "paysupport":
        return ("💬 Помощь с оплатой Stars и возвратами: " + CHANNEL_URL +
                ". Укажите номер заявки и дату платежа. Не передавайте никому токены.")
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
        headers={"Content-Type": "application/json", "X-Pushok-Bot-Secret": secret,
                 "X-Pushok-Telegram-Environment": environment_name()},
        method="POST",
    )
    with urlopen(request, timeout=5) as stream:
        return json.load(stream)


def fetch_current_tariff():
    return _internal_api("tariff-quote", {})


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


def admin_delivery_report(user_id):
    result = _internal_api("admin/delivery-report", {"telegram_user_id": user_id})
    if not isinstance(result, dict) or "advertiser" not in result:
        raise ValueError("Неправильный формат диагностики")
    return result


def admin_retry_advertiser_notice(user_id, notice_id):
    result = _internal_api("admin/retry-advertiser-notice", {
        "telegram_user_id": user_id, "notice_id": str(notice_id),
    })
    if not isinstance(result, dict) or result.get("queued") is not True:
        raise ValueError("API не подтвердил повтор")
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


def deliver_operator_outbox(token):
    """At-least-once отправка. ACK выполняется только после sendMessage."""
    try:
        notices = _internal_api("operators/outbox/claim", {})
        if not isinstance(notices, list):
            raise ValueError("Неверный ответ очереди уведомлений")
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Не удалось получить отложенные уведомления: %s", type(exc).__name__)
        return

    for notice in notices[:5]:
        try:
            notice_id = UUID(str(notice["notice_id"]))
            campaign_id = UUID(str(notice["campaign_id"]))
            recipient = notice["telegram_user_id"]
            if type(recipient) is not int or recipient <= 0:
                raise ValueError("Неверный получатель")
            telegram_call(token, "sendMessage", {
                "chat_id": recipient,
                "text": (
                    "📨 Заявка на модерацию MTProxy Pushok\n"
                    f"Заявка: {str(campaign_id)[:8]}\n"
                    "Цена и срок — в карточке заявки.\n"
                    "Откройте /moderation для проверки."
                ),
                "reply_markup": {"inline_keyboard": [[
                    {"text": "📋 Открыть очередь", "callback_data": "moderation"},
                ]]},
            }, timeout=4)
        except (HTTPError, URLError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            # Сбой доставки: запись останется в PostgreSQL, повтор через 2 мин.
            LOG.warning("Уведомление Telegram не доставлено: %s", type(exc).__name__)
            continue
        try:
            _internal_api("operators/outbox/ack", {"notice_id": str(notice_id)})
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Telegram уже отправил сообщение. Если ACK не записан, возможен дубль.
            LOG.warning("ACK уведомления не сохранён: %s", type(exc).__name__)


def notify_subscribed_operators(campaign_id):
    """Быстрая попытка отправки после submit; при сбое поможет периодический опрос."""
    deliver_operator_outbox(os.getenv("TELEGRAM_BOT_TOKEN", ""))


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
        price = campaign.get("price_kopecks_snapshot")
        currency = (campaign.get("currency_snapshot") or "RUB").strip()
        price_text = (f"{price / 100:g} ₽" if currency == "RUB" and type(price) is int
                      else "Цена в кабинете")
        lines.append(
            f"\n{index}. {title}\n"
            f"Тариф: {campaign.get('tariff_name_snapshot', '—')} · {price_text}\n"
            f"Модерация: {moderation.get(campaign.get('moderation_status'), '—')}\n"
            f"Оплата: {'Не требуется — бесплатно' if price == 0 else payment.get(campaign.get('payment_status'), '—')}\n"
            f"Размещение: {placement.get(campaign.get('placement_status'), '—')}"
        )
    lines.append("\nПоказаны до 10 последних заявок. Подробности доступны в кабинете.")
    return "".join(lines)[:3900]


def command_text(command, actor, chat_id, campaign_loader=None, tariff_loader=None):
    if command == "tariffs" and tariff_loader is not None:
        try:
            return ("💎 Актуальный тариф: " + format_tariff(tariff_loader()) +
                    "\nДоступность оплаты смотрите в карточке одобренной заявки.")
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            LOG.warning("Не удалось прочитать тариф: %s", type(exc).__name__)
            return "💎 Тариф временно недоступен. Проверьте цену в кабинете."
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



def campaign_payment_keyboard(campaigns):
    """Кнопки XTR только для ожидающих оплаты одобренных кампаний."""
    markup = keyboard()
    for item in campaigns[:10]:
        if (item.get("moderation_status") != "approved"
            or item.get("payment_status") != "awaiting_payment"
            or item.get("placement_status") != "not_scheduled"):
            continue
        stars = item.get("stars_price_xtr")
        if type(stars) is not int or not 1 <= stars <= 1000000:
            continue
        try:
            campaign = UUID(str(item["campaign_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        markup["inline_keyboard"].insert(-2, [{
            "text": "⭐ Оплатить " + str(stars) + " XTR · " + str(campaign)[:8],
            "callback_data": "stars:" + str(campaign),
        }])
    return markup


def command_reply(command, actor, chat_id, campaign_loader=None, tariff_loader=None):
    """Одно чтение кампаний: текст и кнопки из одной версии данных."""
    sender = (actor or {}).get("id")
    if command == "campaigns" and campaign_loader is not None:
        if type(sender) is not int or sender <= 0 or sender != chat_id:
            return {"chat_id": chat_id,
                    "text": "Откройте личный чат с ботом для просмотра ваших заявок.",
                    "reply_markup": keyboard()}
        try:
            campaigns = campaign_loader(sender)
            return {"chat_id": chat_id, "text": format_campaigns(campaigns),
                    "reply_markup": campaign_payment_keyboard(campaigns)}
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            LOG.warning("Не удалось получить кампании: %s", type(exc).__name__)
            return {"chat_id": chat_id,
                    "text": "📋 Заявки временно недоступны. Попробуйте позже.",
                    "reply_markup": keyboard()}
    return {"chat_id": chat_id,
            "text": command_text(command, actor, chat_id, campaign_loader, tariff_loader),
            "reply_markup": keyboard()}


def process_stars_update(update, token, internal_api=None, send=None):
    """Обработка trusted update от Telegram Bot API. Вернуть True если обработан.

    Если receipt не записан в БД — выбрасываем исключение и не двигаем offset.
    """
    internal_api = internal_api or _internal_api
    send = send or telegram_call
    pre = update.get("pre_checkout_query")
    if pre is not None:
        query_id = pre.get("id")
        sender = (pre.get("from") or {}).get("id")
        try:
            if not query_id or type(sender) is not int or sender <= 0:
                raise ValueError("bad_precheckout")
            approval = internal_api("stars/precheckout", {
                "query_id": query_id, "telegram_user_id": sender,
                "invoice_payload": pre.get("invoice_payload"),
                "currency": pre.get("currency"),
                "total_amount": pre.get("total_amount"),
            })
            ok = approval.get("ok") is True
        except (HTTPError, URLError, OSError, RuntimeError, ValueError,
                TypeError, AttributeError):
            ok = False
        answer = {"pre_checkout_query_id": query_id, "ok": ok}
        if not ok:
            answer["error_message"] = "Не удалось подтвердить заказ. Создайте новый счёт."
        send(token, "answerPreCheckoutQuery", answer, timeout=4)
        return True

    message = update.get("message") or {}
    paid = message.get("successful_payment")
    if paid is not None:
        sender = (message.get("from") or {}).get("id")
        chat = message.get("chat") or {}
        if (type(sender) is not int or sender <= 0
            or chat.get("id") != sender or chat.get("type") != "private"):
            raise RuntimeError("payment_sender_invalid")
        confirmation = internal_api("stars/settle", {
            "telegram_user_id": sender,
            "invoice_payload": paid.get("invoice_payload"),
            "currency": paid.get("currency"),
            "total_amount": paid.get("total_amount"),
            "telegram_payment_charge_id": paid.get("telegram_payment_charge_id"),
        })
        state = confirmation.get("status")
        if state not in ("applied", "already_processed", "requires_review"):
            raise RuntimeError("receipt_not_persisted")
        text = (
            "✅ Оплата Stars подтверждена. Ожидайте назначения рекламного размещения."
            if state in ("applied", "already_processed")
            else "⚠️ Платёж поступил, но требует сверки. Напишите /paysupport."
        )
        send(token, "sendMessage", {"chat_id": sender, "text": text}, timeout=5)
        return True

    callback = update.get("callback_query") or {}
    action = callback.get("data")
    if not isinstance(action, str) or not action.startswith("stars:"):
        return False
    sender = (callback.get("from") or {}).get("id")
    chat = (callback.get("message") or {}).get("chat") or {}
    if (type(sender) is not int or sender <= 0
        or chat.get("id") != sender or chat.get("type") != "private"):
        return True
    try:
        campaign_id = UUID(action[6:])
        if action != "stars:" + str(campaign_id):
            raise ValueError("bad_campaign_id")
    except (TypeError, AttributeError, ValueError):
        return True
    send(token, "answerCallbackQuery", {"callback_query_id": callback["id"]}, timeout=4)
    try:
        invoice = internal_api("stars/prepare", {
            "telegram_user_id": sender, "campaign_id": str(campaign_id),
        })
        amount = invoice["stars_amount"]
        if (type(amount) is not int or not 1 <= amount <= 1000000
            or not isinstance(invoice["invoice_payload"], str)
            or not invoice["invoice_payload"].startswith("stars:")):
            raise ValueError("bad_invoice")
        # provider_token для Stars не передаётся. start_parameter не даёт
        # использовать пересланную кнопку Pay посторонним пользователям.
        send(token, "sendInvoice", {
            "chat_id": sender,
            "title": str(invoice["title"])[:32],
            "description": str(invoice["description"])[:255],
            "payload": invoice["invoice_payload"],
            "currency": "XTR",
            "prices": [{"label": "Спонсорство MTProxy", "amount": amount}],
            "start_parameter": "pay_" + str(campaign_id).replace("-", ""),
        }, timeout=10)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError,
            KeyError, TypeError) as exc:
        LOG.warning("Не удалось выставить Stars invoice: %s", type(exc).__name__)
        send(token, "sendMessage", {
            "chat_id": sender,
            "text": "Счёт пока недоступен. Проверьте одобрение канала, "
                    "стоимость в Stars и отсутствие предыдущей оплаты.",
        }, timeout=5)
    return True


def response_for(update, campaign_loader=None, wizard=None, submitter=None,
                 operator_inbox=None, delivery_inbox=None, tariff_loader=None):
    """Вернуть (sendMessage_payload, callback_query_id) либо None."""
    if delivery_inbox is not None:
        delivery_reply = delivery_inbox.handle(update)
        if delivery_reply is not None:
            return delivery_reply
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
        return (command_reply(command, callback.get("from"), chat["id"],
                              campaign_loader, tariff_loader), callback.get("id"))
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    content = message.get("text")
    if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
        return None
    if not isinstance(content, str) or not content.startswith("/"):
        return None
    parts = content.split(maxsplit=1)[0][1:].split("@", 1)
    if len(parts) == 2 and parts[1].lower() != configured_bot_username():
        return None
    command = parts[0].lower()
    if command not in COMMANDS:
        return None
    return (command_reply(command, message.get("from"), chat["id"],
                          campaign_loader, tariff_loader), None)


def telegram_call(token, method, payload, timeout=40):
    # Оформляем только меню; запросы счетов, оплаты и возвратов не изменяем.
    if method in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
        payload = with_button_styles(payload)
    # Повтор допустим лишь после однозначного отказа отправить меню из-за иконки.
    # Счета, платежи, таймауты, 429 и 5xx никогда не повторяются этим механизмом.
    plain_payload = without_button_icons(payload) if method in ("sendMessage", "editMessageText") else None
    for attempt in range(2):
        req = Request(bot_api_url(token, method),
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=timeout) as stream:
                result = json.load(stream)
        except HTTPError as exc:
            if attempt or plain_payload is None or exc.code != 400:
                raise
            try:
                raw = exc.read(65537)
                result = json.loads(raw) if len(raw) <= 65536 else None
            except (OSError, ValueError, RecursionError):
                raise exc from None
            finally:
                exc.close()
            if not icon_rejection(result):
                raise
        if not attempt and plain_payload is not None and icon_rejection(result):
            LOG.warning("Telegram отклонил иконку кнопки; одна отправка меню без иконок")
            payload = plain_payload
            continue
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError("Telegram API отклонил запрос")
        return result["result"]


def validate_bot(token):
    """Проверить правильность токена и отсутствие webhook перед polling."""
    info = telegram_call(token, "getMe", {})
    if info.get("username", "").lower() != configured_bot_username():
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
    wizard = DraftWizard(create_campaign_draft, enabled=ready, tariff_loader=fetch_current_tariff)
    operator_inbox = OperatorInbox(operator_queue, operator_decide, operator_unsubscribe)
    delivery_inbox = DeliveryInbox(admin_delivery_report, admin_retry_advertiser_notice)
    navigation = Navigation(telegram_call)
    next_notification_poll = time.monotonic()
    offset = None
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {
                "offset": offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query"]})
            for update in updates:
                next_offset = update["update_id"] + 1
                if process_stars_update(update, token):
                    offset = next_offset
                    continue
                response = response_for(update, campaign_loader=fetch_campaigns, wizard=wizard,
                                        tariff_loader=fetch_current_tariff,
                                        submitter=submit_campaign_draft,
                                        operator_inbox=operator_inbox,
                                        delivery_inbox=delivery_inbox)
                if response is not None:
                    payload, callback_id = response
                    if callback_id:
                        telegram_call(token, "answerCallbackQuery",
                                      {"callback_query_id": callback_id})
                    navigation.deliver(token, update, payload)
                # Не подтверждать update до завершения обработки.
                offset = next_offset
            if ready and time.monotonic() >= next_notification_poll:
                next_notification_poll = time.monotonic() + 30
                deliver_operator_outbox(token)
                deliver_advertiser_notices(
                    _internal_api,
                    lambda payload: telegram_call(token, "sendMessage", payload, timeout=4),
                )
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Не включать str(exc): HTTPError содержит URL с секретным токеном.
            LOG.warning("Ошибка Telegram API: %s, повтор через 5 сек.", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    run()
