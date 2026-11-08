"""Доставка уведомлений рекламодателю. Транспорт передаётся как callback.

Доставка как минимум один раз (at least once): после успешного Telegram
sendMessage, но до записи ACK возможна повторная доставка при аварии.
"""
import logging
from urllib.error import HTTPError, URLError
from uuid import UUID

LOG = logging.getLogger("pushok.bot.advertiser_notices")

_DECISIONS = {
    "approved": "✅ Ваша рекламная заявка одобрена модератором.\n"
                "Статус: ожидание оплаты. Не переводите деньги без официальных "
                "инструкций в кабинете.",
    "rejected": "❌ Ваша рекламная заявка отклонена модератором.",
    "changes_requested": "✏️ Для вашей рекламной заявки нужны исправления.",
}


def render_advertiser_notice(notice):
    """Без Telegram Markdown/HTML: текст модератора не может подменить разметку."""
    decision = notice.get("decision")
    if decision not in _DECISIONS:
        raise ValueError("Неизвестный статус решения")
    campaign = UUID(str(notice["campaign_id"]))
    lines = [
        "🐱 MTProxy Pushok",
        _DECISIONS[decision],
        "Заявка: " + str(campaign)[:8],
    ]
    if decision != "approved":
        comment = notice.get("comment")
        if isinstance(comment, str):
            cleaned = "".join(c for c in comment if c.isprintable() or c == "\n").strip()
            if cleaned:
                lines.append("Комментарий модератора:\n" + cleaned[:1100])
    lines.append("Посмотрите подробности в разделе «Мои кампании» (/campaigns).")
    return "\n\n".join(lines)


def deliver_advertiser_notices(api, sender):
    """Отправить не более пяти уведомлений; не терять их при сбое."""
    try:
        items = api("advertisers/outbox/claim", {})
        if not isinstance(items, list):
            raise ValueError("Неверный формат очереди")
    except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
        LOG.warning("Очередь уведомлений рекламодателям недоступна: %s", type(exc).__name__)
        return

    for item in items[:5]:
        try:
            notice_id = UUID(str(item["notice_id"]))
            token = UUID(str(item["claim_token"]))
            recipient = item["telegram_user_id"]
            if type(recipient) is not int or recipient <= 0:
                raise ValueError("Некорректный Telegram ID")
            sender({
                "chat_id": recipient,
                "text": render_advertiser_notice(item),
                "reply_markup": {"inline_keyboard": [[
                    {"text": "📋 Мои кампании", "callback_data": "campaigns"},
                ]]},
            })
        except HTTPError as exc:
            # Только Telegram 403 (заблокирован бот) считаем постоянным отказом.
            # В остальных случаях оставляем событие на повторную попытку.
            if exc.code == 403:
                try:
                    api("advertisers/outbox/finish", {
                        "notice_id": str(notice_id),
                        "claim_token": str(token),
                        "permanent_failure": True,
                    })
                except (HTTPError, URLError, OSError, ValueError, RuntimeError) as finish_exc:
                    LOG.warning("Не удалось пометить Telegram 403: %s",
                                type(finish_exc).__name__)
            LOG.warning("Telegram не доставил сообщение: HTTP %s", exc.code)
            continue
        except (URLError, OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            LOG.warning("Не удалось отправить уведомление: %s", type(exc).__name__)
            continue
        try:
            result = api("advertisers/outbox/finish", {
                "notice_id": str(notice_id),
                "claim_token": str(token),
                "permanent_failure": False,
            })
            if not isinstance(result, dict) or result.get("acknowledged") is not True:
                raise ValueError("ACK не подтверждён")
        except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
            LOG.warning("Уведомление отправлено, но ACK не сохранён: %s",
                        type(exc).__name__)
