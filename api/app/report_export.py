"""Безопасная CSV-выгрузка уже разрешённых полей отчёта рекламодателя.

Не содержит персональных данных операторов, чеков или приватных скриншотов.
CSV — данные, не подтверждение реального показа спонсора Telegram.
"""
import csv
import io
from datetime import datetime
from typing import Any

PUBLIC_EVENT_TITLES = {
    "campaign_submitted": "Заявка отправлена",
    "campaign_moderated": "Модерация выполнена",
    "payment_proof_submitted": "Чек отправлен",
    "payment_reviewed": "Оплата проверена оператором",
    "sbp_invoice_created": "Выставлен счёт СБП",
    "sbp_payment_verified": "СБП подтверждена сервером",
    "sbp_payment_canceled": "СБП отменена провайдером",
    "placement_scheduled": "Размещение запланировано",
    "placement_activated_manually": "Оператор отметил активацию",
    "placement_completed_manually": "Оператор отметил завершение",
}

def _safe_cell(value: Any) -> str:
    """Нейтрализовать CSV-formula injection даже после начальных пробелов."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    stripped = text.lstrip()
    if (stripped.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))):
        return "'" + text
    return text

def render_campaign_csv(report: dict[str, Any]) -> bytes:
    """UTF-8 BOM, ; delimiter для RU Excel/LibreOffice, безопасные ячейки."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Раздел", "Поле", "Значение"))

    def row(section: str, field: str, value: Any) -> None:
        writer.writerow(tuple(_safe_cell(x) for x in (section, field, value)))

    tariff = report["tariff"]
    obs = report["operator_observations"]
    verify = report["verification"]
    row("Кампания", "ID", report["campaign_id"])
    row("Канал", "Название", report["channel"]["title"])
    row("Канал", "Ссылка", report["channel"]["telegram_url"])
    row("Тариф", "Название", tariff["name"])
    row("Тариф", "Цена, копейки", tariff["price_kopecks"])
    row("Тариф", "Валюта", tariff["currency"])
    row("Тариф", "Длительность, секунд", tariff["duration_seconds"])
    row("Статус", "Модерация", report["moderation_status"])
    row("Статус", "Оплата", report["payment_status"])
    row("Статус", "Размещение", report["placement_status"])
    row("Время", "Запрошенное начало", report["requested_start_at"])
    row("Время", "Начало по плану", report["scheduled_start_at"])
    row("Время", "Окончание по плану", report["scheduled_end_at"])
    row("Оператор", "Показ виден, проверок", obs["visible"])
    row("Оператор", "Показ не обнаружен, проверок", obs["not_visible"])
    row("Оператор", "Всего ручных проверок", obs["total"])
    row("Оператор", "Последний результат", obs["latest_result"])
    row("Оператор", "Время последней проверки", obs["latest_observed_at"])
    row("Оператор", "Количество приватных скриншотов (без доступа)", obs["private_screenshot_count"])
    row("Проверка", "Источник", verify["source"])
    row("Проверка", "Telegram API подтвердил", "нет")
    row("Проверка", "Реальные показы подтверждены", "нет")
    row("Проверка", "Количество показов", "нет данных")
    row("Отчёт", "Сформирован", report["generated_at"])
    for event in report["timeline"]:
        label = PUBLIC_EVENT_TITLES.get(event.get("event"))
        if label:
            row("История", label, event.get("at"))
    row("Примечание", "Достоверность", "Ручные отчёты оператора не доказывают показы Telegram.")
    return output.getvalue().encode("utf-8-sig")
