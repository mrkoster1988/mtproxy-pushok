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


class EvidenceReadError(ValueError):
    """Безопасная ошибка проверки файла: без возврата содержимого."""


def read_verified_image(directory, key, expected_digest, expected_size, mime):
    """Проверить файл по SHA256 и размеру, не следуя symlink. Не проверяет Telegram."""
    import hmac
    import re
    import stat

    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg)", key):
        raise EvidenceReadError("evidence_invalid_metadata")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise EvidenceReadError("evidence_invalid_metadata")
    if not isinstance(expected_size, int) or not 1 <= expected_size <= MAX_BYTES:
        raise EvidenceReadError("evidence_invalid_metadata")
    if mime != ("image/png" if key.endswith(".png") else "image/jpeg"):
        raise EvidenceReadError("evidence_invalid_metadata")
    path = os.path.join(directory, key)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as exc:
        raise EvidenceReadError("evidence_file_missing") from exc
    except OSError as exc:
        raise EvidenceReadError("evidence_storage_unavailable") from exc
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                raise EvidenceReadError("evidence_integrity_failed")
            data = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise EvidenceReadError("evidence_storage_unavailable") from exc
    if len(data) != expected_size or len(data) > MAX_BYTES:
        raise EvidenceReadError("evidence_integrity_failed")
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_digest):
        raise EvidenceReadError("evidence_integrity_failed")
    if mime == "image/png" and not data.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
        raise EvidenceReadError("evidence_integrity_failed")
    if mime == "image/jpeg" and not data.startswith(bytes([255, 216, 255])):
        raise EvidenceReadError("evidence_integrity_failed")
    return data
