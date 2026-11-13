#!/usr/bin/env python3
"""Подготовка конфигурации custom_emoji_id после публикации набора в @Stickers.

Читает реальные Telegram IDs, полученные владельцем, и сверяет их с
`manifest.json` из успешного GitHub Actions. Не обращается к Telegram и
не включает функцию автоматически.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.button_icons import ALLOWED, CONFIG_ENV, is_valid_icon_id
from bot.pushok_emoji_catalog import catalog_ids


def validate(manifest, submitted, catalog=None):
    if (not isinstance(manifest, dict)
            or manifest.get("schema") != "pushok-telegram-emoji-manifest-v1"
            or not isinstance(manifest.get("actions"), dict)
            or set(manifest["actions"]) != ALLOWED):
        raise ValueError("Требуется манифест MTProxy Pushok с шестью действиями бота")
    expected = set(manifest["actions"])
    if not isinstance(submitted, dict) or not submitted:
        raise ValueError("Требуется непустой JSON-объект action → custom_emoji_id")
    unexpected = set(submitted) - expected
    if unexpected:
        raise ValueError("Неизвестные действия: " + ", ".join(sorted(unexpected)))
    for action, value in submitted.items():
        if not is_valid_icon_id(value):
            raise ValueError(f"Некорректный custom_emoji_id для {action}")
    if catalog is not None:
        allowed_ids = catalog_ids(catalog)
        if not set(submitted.values()).issubset(allowed_ids):
            raise ValueError("Выбранный ID отсутствует в каталоге PushokIcons")
    return {key: submitted[key] for key in sorted(submitted)}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющийся ключ JSON: " + key)
        result[key] = value
    return result


def render_config(mapping, output_format):
    if output_format == "env":
        # Валидатор разрешает только известные ключи и цифровые значения.
        compact = json.dumps(mapping, separators=(",", ":"))
        return f"{CONFIG_ENV}='{compact}'\n"
    return json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--ids", required=True, type=Path, help="Файл action → реальный ID из Telegram")
    p.add_argument("--catalog", type=Path, help="Каталог getStickerSet: проверить принадлежность ID набору PushokIcons")
    p.add_argument("--output", required=True, type=Path, help="Готовый JSON для env, НЕ коммитить реальные настройки в main")
    p.add_argument("--format", choices=("json", "env"), default="json",
                   help="env — одна строка для .env; функция автоматически не включается")
    a = p.parse_args()
    try:
        manifest = json.loads(a.manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        ids = json.loads(a.ids.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        catalog = (json.loads(a.catalog.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
                   if a.catalog else None)
        if a.catalog and catalog is None:
            raise ValueError("Каталог не может быть null")
        mapping = validate(manifest, ids, catalog)
    except (OSError, ValueError, RecursionError) as exc:
        p.error(str(exc))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(render_config(mapping, a.format), encoding="utf-8")
    a.output.chmod(0o600)
    print(f"Проверены {len(mapping)} ID. Конфигурация сохранена локально: {a.output}.")
    if not a.catalog:
        print("Каталог не указан: принадлежность ID набору PushokIcons НЕ проверена.")
    print("ID должны быть проверены живым Telegram API до PUSHOK_BUTTON_ICONS_ENABLED=1")


if __name__ == "__main__":
    main()
