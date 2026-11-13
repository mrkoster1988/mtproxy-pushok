#!/usr/bin/env python3
"""Получить каталог PushokIcons через getStickerSet. Не отправляет сообщения."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.pushok_emoji_catalog import PACK_NAME, build_catalog
from bot.telegram_environment import bot_api_url, is_test_environment

MAX_RESPONSE = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_catalog(token, expected_count=12):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,128}", token):
        raise ValueError("Некорректный формат TELEGRAM_BOT_TOKEN")
    if is_test_environment():
        raise ValueError("Опубликованный PushokIcons запрашивается в основной среде Telegram")
    request = Request(bot_api_url(token, "getStickerSet"),
                      data=json.dumps({"name": PACK_NAME}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    # Токен остаётся на фиксированном HTTPS-хосте, без redirect и proxy из окружения.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        exc.close()
        raise ValueError("Telegram отклонил getStickerSet; проверьте токен и доступность набора") from None
    except (URLError, OSError, ValueError):
        raise ValueError("Не удалось получить каталог Telegram; проверьте соединение") from None
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Ответ Telegram превышает допустимый размер")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Некорректный JSON в ответе Telegram") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("Telegram не подтвердил получение набора")
    return build_catalog(result.get("result"), expected_count)


def save_catalog(path, catalog):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pushok-emoji-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-count", type=int, default=12,
                        help="Ожидается 12; меняйте только после проверки обновлённого набора")
    args = parser.parse_args()
    if not 1 <= args.expected_count <= 200:
        parser.error("Ожидаемое число значков должно быть от 1 до 200")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token and sys.stdin.isatty():
        token = getpass.getpass("Токен бота (ввод скрыт, в файлы не сохраняется): ")
    if not token:
        parser.error("Нужен TELEGRAM_BOT_TOKEN в окружении или скрытый ввод в терминале")
    try:
        catalog = fetch_catalog(token, args.expected_count)
        save_catalog(args.output, catalog)
    except ValueError as exc:
        parser.error(str(exc))
    except OSError:
        parser.error("Не удалось сохранить каталог; проверьте путь и права")
    print(f"Получено {args.expected_count} значков PushokIcons. Каталог сохранён локально.")
    print("Сопоставьте действия по изображению: порядок и обычный emoji не определяют назначение.")
    print("Меню, настройки и флаг включения не изменены. Доставка в Telegram не проверена.")


if __name__ == "__main__":
    main()
