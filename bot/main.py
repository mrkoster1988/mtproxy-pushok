"""Минимальный Telegram-бот MTProxy Pushok. Python standard library."""
import json
from html import escape
import io
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
from .vpn_notices import deliver as deliver_vpn_notices
from .advertiser_notices import deliver_advertiser_notices
from .operator_inbox import OperatorInbox
from .lifecycle_notices import deliver_lifecycle_notices
from .delivery_inbox import DeliveryInbox
from .button_icons import decorate, configured_icons, without_button_icons, icon_rejection
from .button_styles import with_button_styles
from .proxy_link import current_proxy_url
from .campaign_analytics import member_payload
from .verified_profiles import verification_response, deliver_birthdays

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
_navigation = None


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
    """Главное меню: одна зелёная кнопка размещения рекламы."""
    button = {"text": "📢 Подключить трафик", "style": "success"}
    url = miniapp_url()
    if url:
        button['web_app'] = {'url': url + '#/create'}
    else:
        button['callback_data'] = 'advertise'
    return {"inline_keyboard": [[decorate(button, 'advertise', configured_icons())]]}


def message_for(command):
    if command == "start":
        text = ("🐱 Привет! Я Пушок. Добро пожаловать в MTProxy Pushok!\n\n"
                "Подключай прокси для Telegram и размещай рекламу своего канала. "
                "Заявки, оплата и статусы кампаний — в рекламном кабинете.")
        proxy = current_proxy_url()
        if proxy:
            text += '\n\n🔗 Ссылка для подключения:\n' + proxy
        return text + '\n\nВыбери действие в меню ниже. Всё нужное — под лапой! 🐾'
    if command == "advertise":
        return "📢 Для размещения рекламы открой кабинет или напиши в поддержку:\n" + CHANNEL_URL
    if command == "campaigns":
        return "📋 Кампании временно недоступны. Попробуй позже или открой кабинет."
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
            # Уведомление должно быть новым сообщением, а не правкой меню навигации.
            title = " ".join(str(notice.get("channel_title") or "Канал").split())[:160]
            name = " ".join(str(notice.get("advertiser_name") or "Пользователь").split())[:160]
            name = escape(name)
            username = notice.get("advertiser_username")
            if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
                name += " (@" + username + ")"
            elif "@" in name or "t.me/" in name or "tg://" in name:
                name = f"<code>{name}</code>"
            text = ("📨 Новая кампания на модерации\n"
                    f"Канал: {escape(title)}\n"
                    f"Рекламодатель: {name}\n"
                    f"Заявка: {str(campaign_id)[:8]}\n"
                    "Цена и срок — в карточке заявки.")
            url = miniapp_url()
            button = ({"text": "📋 Открыть заявку", "web_app": {"url": url + "#/moderation/" + str(campaign_id)}}
                      if url else {"text": "📋 Открыть очередь", "callback_data": "moderation"})
            raw_telegram_call(token, "sendMessage", {
                "chat_id": recipient, "text": text, "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[button]]},
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
                    "Статус проверки и дальнейшие действия доступны в карточке заявки.")
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
                "Нажми «Подключить трафик», чтобы создать первую кампанию.")
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


def stars_route(payload, action):
    for prefix, group in (('shop-vpn:', 'vpn-stars'), ('license:', 'license'), ('wallet:', 'wallet')):
        if str(payload).startswith(prefix):
            return group + '/' + action
    return ('wallet' if action == 'refund' else 'stars') + '/' + action


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
            approval = internal_api(stars_route(pre.get("invoice_payload"), "precheckout"), {
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
        is_wallet = str(paid.get("invoice_payload", "")).startswith("wallet:")
        is_license = str(paid.get("invoice_payload", "")).startswith("license:")
        confirmation = internal_api(stars_route(paid.get("invoice_payload"), "settle"), {
            "telegram_user_id": sender,
            "invoice_payload": paid.get("invoice_payload"),
            "currency": paid.get("currency"),
            "total_amount": paid.get("total_amount"),
            "telegram_payment_charge_id": paid.get("telegram_payment_charge_id"),
        })
        state = confirmation.get("status")
        if state not in ("applied", "already_processed", "requires_review"):
            raise RuntimeError("receipt_not_persisted")
        if str(paid.get("invoice_payload", "")).startswith("shop-vpn:"):
            # Shop enqueues its payment/subscription messages in the same transaction.
            return True
        text = (
            "✅ Оплата Stars подтверждена. Ожидайте назначения рекламного размещения."
            if state in ("applied", "already_processed")
            else "⚠️ Платёж поступил, но требует сверки. Напишите /paysupport."
        )
        if is_wallet and state in ("applied", "already_processed"):
            text = ("Тестовое пополнение обработано. Рабочий баланс не увеличен." if confirmation.get('test_mode')
                    else "Пополнение Stars обработано. Остаток и журнал доступны в кабинете: Профиль → Баланс.")
        if is_license and state in ("applied", "already_processed"):
            text = ("Тестовая оплата лицензии обработана. Лицензия установки не активирована." if confirmation.get("test_mode") else "Оплата лицензии приложения подтверждена. Откройте Профиль → Лицензия приложения в своей установке.")
        send(token, "sendMessage", {"chat_id": sender, "text": text}, timeout=5)
        return True

    refunded = message.get("refunded_payment")
    if refunded is not None:
        chat = message.get("chat") or {}
        sender = chat.get("id")
        if type(sender) is not int or sender <= 0 or chat.get("type") != "private":
            raise RuntimeError("refund_sender_invalid")
        route = stars_route(refunded.get("invoice_payload"), "refund")
        extra = ({"invoice_payload": refunded["invoice_payload"], "occurred_at": message.get("date")}
                 if route == "vpn-stars/refund" else {})
        result = internal_api(route, {
            **extra,
            "telegram_user_id": sender, "currency": refunded.get("currency"),
            "total_amount": refunded.get("total_amount"),
            "telegram_payment_charge_id": refunded.get("telegram_payment_charge_id"),
        })
        if result.get("status") != "recorded": raise RuntimeError("refund_not_persisted")
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
                 operator_inbox=None, delivery_inbox=None, tariff_loader=None, referral_handler=None):
    """Вернуть (sendMessage_payload, callback_query_id) либо None."""
    # Старые кнопки и /advertise тоже открывают ту же единую форму.
    launch = miniapp_url()
    callback = update.get('callback_query') or {}
    message = update.get('message') or {}
    chat = ((callback.get('message') or {}) if callback else message).get('chat') or {}
    actor = (callback or message).get('from') or {}
    command = str(message.get('text') or '').split(maxsplit=1)
    command = command[0].lower() if command else ''
    if command in ('/subscriptions', '/subscriptions@' + configured_bot_username().lower()):
        if (chat.get('type') != 'private' or type(actor.get('id')) is not int
                or actor.get('is_bot') or actor['id'] != chat.get('id')):
            return None
        if not launch:
            return ({'chat_id': chat['id'], 'text': 'Кабинет магазина пока не настроен.'}, None)
        buttons = [[{'text':'Открыть VPN', 'web_app':{'url':launch+'?vpn=home'}}]]
        return ({'chat_id':chat['id'], 'text':'Подписка Pushok Flow и срок доступа.',
                 'reply_markup':{'inline_keyboard':buttons}}, None)
    stars = re.fullmatch(r'/start(?:@'+re.escape(configured_bot_username())+r')?\s+stars', str(message.get('text') or ''), re.IGNORECASE)
    if stars and chat.get('type') == 'private' and type(actor.get('id')) is int and actor['id'] == chat.get('id'):
        return ({'chat_id':chat['id'], 'text':'⭐ <a href="tg://settings/stars">Открыть мои Stars</a>\n\nЕсли Telegram не открывает ссылку: Настройки → Мои звёзды. Это личный баланс Telegram.', 'parse_mode':'HTML', 'reply_markup':keyboard()}, None)
    flow = re.fullmatch(r'(?i:/start(?:@'+re.escape(configured_bot_username())+r')?)\s+flow_([23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12})', str(message.get('text') or ''))
    if flow:
        if (chat.get('type') != 'private' or type(actor.get('id')) is not int
                or type(chat.get('id')) is not int or actor['id'] <= 0
                or actor['id'] != chat['id'] or actor.get('is_bot')):
            return None
        if not launch:
            return ({'chat_id': chat['id'], 'text': 'Вход через кабинет пока недоступен. Вернитесь в Pushok Flow и попробуйте позже.'}, None)
        public_code = flow[1]
        display_code = ' '.join(public_code[index:index + 4] for index in range(0, 12, 4))
        return ({'chat_id': chat['id'],
                 'text': 'Вход в Pushok Flow\n\nКод: ' + display_code +
                         '\n\nОткройте кабинет и сверьте код с приложением. Аккаунт подключится только после вашего подтверждения.',
                 'reply_markup': {'inline_keyboard': [[{
                     'text': 'Подтвердить в кабинете',
                     'web_app': {'url': launch + '?flow=' + public_code},
                 }]]}}, None)
    invite = re.fullmatch(r'/start(?:@'+re.escape(configured_bot_username())+r')?\s+(?:ref_|r_)([A-Za-z0-9_-]{3,48})', str(message.get('text') or ''), re.IGNORECASE)
    if invite and referral_handler and chat.get('type') == 'private' and type(actor.get('id')) is int and actor['id'] == chat.get('id'):
        try:
            referral_handler(actor['id'], invite[1])
            text = 'Приглашение сохранено. Откройте кабинет и создайте размещение. Условия бонусов — в профиле → «Бонусы и приглашения».'
        except HTTPError as exc:
            if exc.code not in (400,403,404,409,422):
                raise
            text = 'Приглашение не применено: программа может быть выключена, приглашение уже выбрано или первая заявка уже создана. Кабинет доступен по кнопке ниже.'
        return ({'chat_id':chat['id'], 'text':text, 'reply_markup':keyboard()}, None)
    advertise = callback.get('data') == 'advertise' or command in ('/advertise', '/advertise@' + configured_bot_username().lower())
    if launch and advertise and chat.get('type') == 'private' and type(actor.get('id')) is int and actor.get('id') == chat.get('id'):
        if wizard is not None:
            wizard.sessions.pop(actor['id'], None)
        return ({'chat_id': chat['id'], 'text': '📢 Канал, тариф, дата и отправка на проверку — на одной странице. После одобрения оплата появится в этой же заявке.', 'reply_markup': keyboard()}, callback.get('id'))
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
    if _navigation is not None and method in ('sendMessage', 'sendInvoice'):
        return _navigation.managed_call(token, method, payload, timeout=timeout)
    return raw_telegram_call(token, method, payload, timeout=timeout)


def raw_telegram_call(token, method, payload, timeout=40):
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
                # Навигации нужны описания not modified / not found после чтения тела.
                raise HTTPError(exc.url, exc.code, exc.msg, exc.headers, io.BytesIO(raw)) from None
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
    global _navigation
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
    operator_inbox = OperatorInbox(operator_queue, operator_decide, operator_unsubscribe, miniapp_loader=miniapp_url)
    delivery_inbox = DeliveryInbox(admin_delivery_report, admin_retry_advertiser_notice)
    navigation = Navigation(raw_telegram_call, path=os.getenv('PUSHOK_BOT_NAV_DB'))
    _navigation = navigation
    next_notification_poll = time.monotonic()
    offset = None
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {
                "offset": offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query", "chat_member"]})
            for update in updates:
                next_offset = update["update_id"] + 1
                join=member_payload(update)
                if join and ready:
                    _internal_api("analytics/join",join)
                    offset=next_offset
                    continue
                if ready:
                    verified = verification_response(update, _internal_api,
                        lambda method, payload: raw_telegram_call(token, method, payload, timeout=3))
                    if verified is not None:
                        raw_telegram_call(token, "sendMessage", verified)
                        offset = next_offset
                        continue
                if process_stars_update(update, token):
                    navigation.incoming(token, update)
                    offset = next_offset
                    continue
                response = response_for(update, campaign_loader=fetch_campaigns, wizard=wizard,
                                        tariff_loader=fetch_current_tariff,
                                        submitter=submit_campaign_draft,
                                        operator_inbox=operator_inbox,
                                        delivery_inbox=delivery_inbox,
                                        referral_handler=lambda uid,code: _internal_api('referral', {'telegram_user_id':uid,'code':code}))
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
                    lambda payload: raw_telegram_call(token, "sendMessage", payload, timeout=4),
                )
                deliver_lifecycle_notices(_internal_api,lambda payload: raw_telegram_call(token,"sendMessage",payload,timeout=4),miniapp_url())
                deliver_vpn_notices(_internal_api, lambda payload: raw_telegram_call(token,"sendMessage",payload,timeout=4), miniapp_url() or "https://pushok.shop/miniapp")
                deliver_birthdays(_internal_api, lambda payload: raw_telegram_call(token, "sendMessage", payload, timeout=4), miniapp_url())
                navigation.cleanup_pending(token)
        except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
            # Не включать str(exc): HTTPError содержит URL с секретным токеном.
            LOG.warning("Ошибка Telegram API: %s%s, повтор через 5 сек.", type(exc).__name__,
                        (' HTTP ' + str(exc.code)) if isinstance(exc, HTTPError) else '')
            time.sleep(5)


if __name__ == "__main__":
    run()
