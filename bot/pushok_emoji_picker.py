"""Локальная страница визуального выбора без сети, токена и назначения по порядку."""
import base64
import hashlib
import io
import json
from pathlib import Path
from .pushok_emoji_catalog import catalog_ids

ACTIONS = {"advertise": "Разместить рекламу", "campaigns": "Мои кампании",
           "tariffs": "Тарифы", "channels": "Наш канал", "help": "Помощь",
           "miniapp": "Открыть Mini App"}
TEMPLATE = Path(__file__).resolve().parent / "templates" / "pushok_emoji_picker.html"


def require_image_support():
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("Для --html нужен Pillow: python3 -m pip install 'Pillow>=11,<13'") from None
    return Image


def normalize_preview(raw):
    Image = require_image_support()
    if not isinstance(raw, bytes) or len(raw) > 512 * 1024:
        raise ValueError("Слишком большое изображение")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("WEBP", "JPEG", "PNG") or not (0 < image.width <= 1024 and 0 < image.height <= 1024):
                raise ValueError("Неподдерживаемое изображение")
            image.load()
            image = image.convert("RGBA")
            image.info.clear()
            image.thumbnail((160, 160))
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("Изображение повреждено или имеет неподдерживаемый формат") from None


def render_picker(catalog, previews):
    allowed = catalog_ids(catalog)
    if not isinstance(previews, dict) or not set(previews).issubset(allowed):
        raise ValueError("Изображение отсутствует в каталоге")
    stickers = []
    for sticker in catalog["sticker_set"]["stickers"]:
        icon_id = sticker["custom_emoji_id"]
        raw = previews.get(icon_id)
        image = ("data:image/png;base64," + base64.b64encode(normalize_preview(raw)).decode("ascii")) if raw is not None else None
        stickers.append({"id": icon_id, "emoji": sticker["emoji"], "image": image})
    # JSON в инертном script-блоке; символ < исключает закрытие блока внешним текстом.
    data = json.dumps({"actions": ACTIONS, "stickers": stickers}, ensure_ascii=True).replace("<", "\\u003c")
    template = TEMPLATE.read_text(encoding="utf-8")
    script = template.split('<script id="picker-script">', 1)[1].split('</script>', 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    return template.replace("__SCRIPT_HASH__", digest).replace("__CATALOG_DATA__", data)
