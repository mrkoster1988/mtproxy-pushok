"""Фиксированный адрес Bot API; неверный режим не переключает на production."""
import os
import re
from fastapi import HTTPException


def is_test_environment():
    value = os.getenv("PUSHOK_TELEGRAM_TEST_ENVIRONMENT", "0")
    if value not in ("0", "1"):
        raise HTTPException(503, "invalid_telegram_environment")
    return value == "1"


def environment_name():
    return "test" if is_test_environment() else "production"


def bot_api_url(token, method):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_:-]+", token):
        raise ValueError("invalid_telegram_token")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", method):
        raise ValueError("invalid_telegram_method")
    return "https://api.telegram.org/bot" + token + ("/test/" if is_test_environment() else "/") + method
