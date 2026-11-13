"""Проверка каталога опубликованного набора без назначения действий по порядку."""
from .button_icons import is_valid_icon_id

PACK_NAME = "PushokIcons"
CATALOG_SCHEMA = "pushok-telegram-emoji-catalog-v1"


def valid_file_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 1024
            and all(33 <= ord(c) <= 126 for c in value))


def validate_sticker_set(sticker_set, expected_count=12):
    if type(expected_count) is not int or not 1 <= expected_count <= 200:
        raise ValueError("Ожидаемое число значков должно быть от 1 до 200")
    if (not isinstance(sticker_set, dict)
            or sticker_set.get("name") != PACK_NAME
            or sticker_set.get("sticker_type") != "custom_emoji"):
        raise ValueError("Требуется набор Custom Emoji PushokIcons")
    stickers = sticker_set.get("stickers")
    if not isinstance(stickers, list) or len(stickers) != expected_count:
        raise ValueError("Число значков не совпадает с ожидаемым; проверьте набор")
    seen = set()
    cleaned = []
    for sticker in stickers:
        if not isinstance(sticker, dict) or sticker.get("type") != "custom_emoji":
            raise ValueError("Неверный тип значка")
        icon_id = sticker.get("custom_emoji_id")
        if not is_valid_icon_id(icon_id) or icon_id in seen:
            raise ValueError("Некорректный или повторяющийся custom_emoji_id")
        seen.add(icon_id)
        file_id, emoji = sticker.get("file_id"), sticker.get("emoji")
        if not valid_file_id(file_id):
            raise ValueError("Отсутствует или повреждён file_id")
        # Поле emoji в Bot API необязательно; отсутствие не угадываем.
        if emoji is not None and (not isinstance(emoji, str) or not emoji or len(emoji) > 64
                                  or any(ord(c) < 32 for c in emoji)):
            raise ValueError("Некорректный связанный emoji")
        entry = {"type": "custom_emoji", "custom_emoji_id": icon_id,
                 "emoji": emoji, "file_id": file_id}
        for flag in ("is_animated", "is_video"):
            if flag in sticker:
                if type(sticker[flag]) is not bool:
                    raise ValueError("Некорректный формат значка")
                entry[flag] = sticker[flag]
        thumbnail = sticker.get("thumbnail")
        if thumbnail is not None:
            if not isinstance(thumbnail, dict) or not valid_file_id(thumbnail.get("file_id")):
                raise ValueError("Некорректная миниатюра значка")
            entry["thumbnail"] = {"file_id": thumbnail["file_id"]}
        cleaned.append(entry)
    return {"name": PACK_NAME, "sticker_type": "custom_emoji", "stickers": cleaned}


def build_catalog(sticker_set, expected_count=12):
    return {"schema": CATALOG_SCHEMA, "environment": "production",
            "expected_count": expected_count,
            "sticker_set": validate_sticker_set(sticker_set, expected_count)}


def catalog_ids(catalog):
    if (not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA
            or catalog.get("environment") != "production"):
        raise ValueError("Требуется каталог PushokIcons из основной среды Telegram")
    validated = validate_sticker_set(catalog.get("sticker_set"), catalog.get("expected_count"))
    return {sticker["custom_emoji_id"] for sticker in validated["stickers"]}
