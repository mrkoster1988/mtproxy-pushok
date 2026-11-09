"""Защищённые изображения отчётов оператора, не проверка Telegram API."""
import hashlib
import io
import os
from uuid import uuid4
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 8_000_000

def sanitize_image(upload):
    raw = upload.file.read(MAX_BYTES + 1)
    if not raw:
        raise ValueError("evidence_empty")
    if len(raw) > MAX_BYTES:
        raise ValueError("evidence_too_large")
    allowed = {"image/png": ("PNG", ".png"), "image/jpeg": ("JPEG", ".jpg")}
    expected = allowed.get(upload.content_type)
    if not expected:
        raise ValueError("unsupported_evidence_format")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != expected[0] or getattr(im, "n_frames", 1) != 1:
                raise ValueError("evidence_format_mismatch")
            if not 0 < im.width * im.height <= MAX_PIXELS:
                raise ValueError("evidence_dimensions_invalid")
            im.load()
            # Перекодировать без исходных EXIF и прочих метаданных.
            clean = im.convert("RGB" if expected[0] == "JPEG" else "RGBA")
            out = io.BytesIO()
            if expected[0] == "JPEG":
                clean.save(out, format="JPEG", quality=85, optimize=True)
            else:
                clean.save(out, format="PNG", optimize=True)
    except ValueError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("evidence_corrupt_image") from exc
    result = out.getvalue()
    if len(result) > MAX_BYTES:
        raise ValueError("evidence_too_large")
    return result, upload.content_type, expected[1], hashlib.sha256(result).hexdigest()

def store_image(data, suffix, directory):
    if suffix not in (".png", ".jpg"):
        raise ValueError("unsupported_evidence_format")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    key = uuid4().hex + suffix
    filename = os.path.join(directory, key)
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
    except OSError:
        if os.path.isfile(filename):
            os.unlink(filename)
        raise
    return key, filename
