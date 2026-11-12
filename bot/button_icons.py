"""Безопасные, необязательные иконки Telegram-кнопок Pushok.

Telegram принимает только зарегистрированные custom_emoji_id; SVG/PNG не подходят.
Отсутствующая, повреждённая или неразрешённая конфигурация сохраняет обычное меню.
"""
import json
import os
import re

ALLOWED = frozenset(["advertise","campaigns","tariffs","channels","help","miniapp"])
_VALID_ID = re.compile(r"^[0-9]{5,32}$")
CONFIG_ENV = "PUSHOK_BUTTON_EMOJI_JSON"
ENABLED_ENV = "PUSHOK_BUTTON_ICONS_ENABLED"


def is_valid_icon_id(value):
    """Проверка формата; существование ID подтверждается только в Telegram."""
    return (isinstance(value, str) and _VALID_ID.fullmatch(value) is not None
            and int(value) > 0)


def configured_icons():
    if os.getenv(ENABLED_ENV) != "1":
        return {}
    raw = os.getenv(CONFIG_ENV, "")
    if not raw or len(raw) > 4096:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value for key, value in data.items()
        if key in ALLOWED and is_valid_icon_id(value)
    }


def decorate(button, action, icons=None):
    """Возвращает новый словарь кнопки, не меняя callback/url/настройки покупки."""
    icon_id = (configured_icons() if icons is None else icons).get(action)
    result = dict(button)
    if action in ALLOWED and is_valid_icon_id(icon_id):
        result["icon_custom_emoji_id"] = icon_id
    return result
