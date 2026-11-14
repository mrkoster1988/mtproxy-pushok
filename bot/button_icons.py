"""Безопасные, необязательные иконки Telegram-кнопок Pushok.

Telegram принимает только зарегистрированные custom_emoji_id; SVG/PNG не подходят.
Отсутствующая, повреждённая или неразрешённая конфигурация сохраняет обычное меню.
"""
import json
import os
import re
from pathlib import Path

ALLOWED = frozenset(["advertise","campaigns","tariffs","channels","help","miniapp"])
_VALID_ID = re.compile(r"^[0-9]{5,32}$")
CONFIG_ENV = "PUSHOK_BUTTON_EMOJI_JSON"
ENABLED_ENV = "PUSHOK_BUTTON_ICONS_ENABLED"


def is_valid_icon_id(value):
    """Проверка формата; существование ID подтверждается только в Telegram."""
    return (isinstance(value, str) and _VALID_ID.fullmatch(value) is not None
            and int(value) > 0)


def configured_icons():
    try:
        configured = json.loads(Path('/var/lib/mtproxy-pushok/control/emoji-map.json').read_text())
        if isinstance(configured, dict):
            return {k:v for k,v in configured.items() if k in ALLOWED and is_valid_icon_id(v)}
    except (OSError, ValueError):
        pass
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
        result["text"] = re.sub(r"^[^\w]+", "", result.get("text", ""))
    return result


def without_button_icons(payload):
    """Копия сообщения без декоративных полей; None, если удалять нечего."""
    markup = payload.get("reply_markup")
    if not isinstance(markup, dict) or not isinstance(markup.get("inline_keyboard"), list):
        return None
    rows = markup["inline_keyboard"]
    if any(not isinstance(row, list) or any(not isinstance(b, dict) for b in row) for row in rows):
        return None
    if not any("icon_custom_emoji_id" in button for row in rows for button in row):
        return None
    ordinary = {'Разместить рекламу':'📢','Мои кампании':'📋','Тарифы':'💎','Наш канал':'📣','Помощь':'❓'}
    def restore(button):
        result={key:value for key,value in button.items() if key!='icon_custom_emoji_id'}
        label=result.get('text','')
        if label in ordinary:
            result['text']=ordinary[label]+' '+label
        return result
    return {**payload, "reply_markup": {**markup, "inline_keyboard": [
        [restore(button) for button in row] for row in rows]}}


def icon_rejection(result):
    """Только явный отказ по иконке, без повторов по таймауту/общим ошибкам."""
    if (not isinstance(result, dict) or result.get("ok") is not False
            or result.get("error_code") != 400):
        return False
    description = result.get("description")
    if not isinstance(description, str):
        return False
    description = description.lower()
    return ("icon_custom_emoji_id" in description or "button_icon_invalid" in description
            or ("keyboard button" in description and "custom emoji" in description))
