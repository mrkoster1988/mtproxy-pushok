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
from bot.pushok_emoji_catalog import PACK_NAME, build_catalog, catalog_ids
from bot.telegram_environment import bot_api_url, is_test_environment

MAX_RESPONSE = 1024 * 1024
MAX_PREVIEW_BYTES = 512 * 1024
MAX_PREVIEWS_TOTAL = 8 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,128}", token):
        raise ValueError("Некорректный формат TELEGRAM_BOT_TOKEN")
    if is_test_environment():
        raise ValueError("Опубликованный PushokIcons запрашивается в основной среде Telegram")


def read_telegram(request, limit):
    # Токен остаётся на фиксированном HTTPS-хосте, без redirect и proxy из окружения.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(limit + 1)
    except HTTPError as exc:
        exc.close()
        raise ValueError("Telegram отклонил запрос; проверьте токен и доступность набора") from None
    except (URLError, OSError, ValueError):
        raise ValueError("Не удалось получить каталог Telegram; проверьте соединение") from None
    if len(raw) > limit:
        raise ValueError("Ответ Telegram превышает допустимый размер")
    return raw


def read_method(token, method, payload):
    validate_token(token)
    if method not in ("getStickerSet", "getFile"):
        raise ValueError("Разрешено только чтение набора и файлов")
    request = Request(bot_api_url(token, method), data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    raw = read_telegram(request, MAX_RESPONSE)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Некорректный JSON в ответе Telegram") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("Telegram не подтвердил получение набора")
    return result.get("result")


def fetch_catalog(token, expected_count=12):
    return build_catalog(read_method(token, "getStickerSet", {"name": PACK_NAME}), expected_count)


def fetch_previews(token, catalog):
    """Скачивает только изображения: URL с токеном никогда не попадает в HTML."""
    from bot.pushok_emoji_picker import normalize_preview
    validate_token(token)
    catalog_ids(catalog)
    previews = {}
    total = 0
    for sticker in catalog["sticker_set"]["stickers"]:
        file_id = (sticker.get("thumbnail") or {}).get("file_id")
        if not file_id and (sticker.get("is_animated") or sticker.get("is_video")):
            continue  # Для анимации нужна статическая миниатюра; TGS/WebM не исполняем.
        file_id = file_id or sticker["file_id"]
        try:
            info = read_method(token, "getFile", {"file_id": file_id})
            path = info.get("file_path") if isinstance(info, dict) else None
            if (not isinstance(path, str) or len(path) > 512
                    or not re.fullmatch(r"[A-Za-z0-9_/-]+\.(?:webp|jpg|jpeg|png)", path)
                    or any(part in ("", ".", "..") for part in path.split("/"))):
                continue
            size = info.get("file_size")
            if size is not None and (type(size) is not int or not 0 < size <= MAX_PREVIEW_BYTES):
                continue
            request = Request("https://api.telegram.org/file/bot" + token + "/" + path)
            raw = read_telegram(request, MAX_PREVIEW_BYTES)
            total += len(raw)
            if total > MAX_PREVIEWS_TOTAL:
                break
            previews[sticker["custom_emoji_id"]] = normalize_preview(raw)
        except ValueError:
            # Не выводим исключение/URL: вместо догадки страница покажет отсутствие картинки.
            continue
    return previews


def save_catalog(path, catalog):
    save_text(path, json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")


def save_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pushok-emoji-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--html", type=Path, help="Локальная страница выбора по изображениям; требуется Pillow")
    parser.add_argument("--expected-count", type=int, default=12,
                        help="Ожидается 12; меняйте только после проверки обновлённого набора")
    args = parser.parse_args()
    if not 1 <= args.expected_count <= 200:
        parser.error("Ожидаемое число значков должно быть от 1 до 200")
    if args.html:
        if args.html.resolve() == args.output.resolve():
            parser.error("Каталог JSON и страница HTML должны иметь разные пути")
        try:
            from bot.pushok_emoji_picker import render_picker, require_image_support
            require_image_support()
        except ValueError as exc:
            parser.error(str(exc))
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token and sys.stdin.isatty():
        token = getpass.getpass("Токен бота (ввод скрыт, в файлы не сохраняется): ")
    if not token:
        parser.error("Нужен TELEGRAM_BOT_TOKEN в окружении или скрытый ввод в терминале")
    try:
        catalog = fetch_catalog(token, args.expected_count)
        if args.html:
            previews = fetch_previews(token, catalog)
            page = render_picker(catalog, previews)
        save_catalog(args.output, catalog)
        if args.html:
            save_text(args.html, page)
    except ValueError as exc:
        parser.error(str(exc))
    except OSError:
        parser.error("Не удалось сохранить выходные файлы; проверьте путь и права")
    print(f"Получено {args.expected_count} значков PushokIcons. Каталог сохранён локально.")
    print("Сопоставьте действия по изображению: порядок и обычный emoji не определяют назначение.")
    if args.html:
        print(f"Страница выбора сохранена локально. Изображений: {len(previews)} из {args.expected_count}.")
    print("Меню, настройки и флаг включения не изменены. Доставка в Telegram не проверена.")


if __name__ == "__main__":
    main()
