#!/usr/bin/env python3
"""Разовый help.getPromoData через MTProxy: только временная сессия в памяти."""
import argparse
import asyncio
import getpass
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import warnings
from datetime import datetime, timezone

TELETHON_VERSION = "1.45.0"
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")


def load_proxy(path):
    """Читаем три поля без shell, исполнения env и вывода секрета."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                or info.st_size > 65536):
            raise ValueError("unsafe_proxy_config")
        text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError("oversized_proxy_config")
    cfg = {}
    for line in text.splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        key = key.strip()
        if sep and key in {"MT_PROXY_HOST", "MT_PROXY_PORT", "MT_PROXY_SECRET"}:
            parts = shlex.split(value, comments=True)
            if key in cfg or len(parts) != 1:
                raise ValueError("invalid_proxy_config")
            cfg[key] = parts[0]
    host = cfg["MT_PROXY_HOST"]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", host):
            raise ValueError("invalid_proxy_host") from None
    port = int(cfg["MT_PROXY_PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("invalid_proxy_port")
    secret = cfg["MT_PROXY_SECRET"].lower()
    if len(secret) == 34 and secret.startswith("dd"):
        secret = secret[2:]
    if not re.fullmatch(r"[0-9a-f]{32}", secret):
        raise ValueError("unsupported_proxy_secret")
    return host, port, secret


def expiry_utc(value):
    if type(value) is int:
        value = datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


def summarize_promo(response, expected):
    """Не сериализуем сырой ответ: в нём есть access_hash и чужие сущности."""
    result = {
        "outcome": "no_proxy_promo",
        "proxy": bool(getattr(response, "proxy", False)),
        "expires_utc": expiry_utc(getattr(response, "expires", None)),
        "display_verified": False,
    }
    peer = getattr(response, "peer", None)
    channel_id = getattr(peer, "channel_id", None)
    if not result["proxy"] or peer is None:
        return result
    result["outcome"] = "proxy_promo_unresolved"
    if type(channel_id) is not int:
        return result
    channel = next((chat for chat in getattr(response, "chats", [])
                    if getattr(chat, "id", None) == channel_id), None)
    if channel is None:
        return result
    usernames = [getattr(channel, "username", None)]
    usernames += [getattr(name, "username", None)
                  for name in (getattr(channel, "usernames", None) or [])
                  if getattr(name, "active", False)]
    usernames = sorted({name.lower() for name in usernames
                        if isinstance(name, str) and USERNAME.fullmatch(name)})
    result["channel_usernames"] = usernames
    if usernames:
        result["outcome"] = ("proxy_promo_expected" if expected.lower() in usernames
                             else "proxy_promo_other")
    for key in ("left", "kicked", "restricted"):
        value = getattr(channel, key, None)
        result["channel_" + key] = value if type(value) is bool else None
    return result


def hidden(prompt):
    # Не допускаем перехода getpass к открытому вводу при отсутствии TTY.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt).strip()


async def probe(client, expected, password_needed, invalid_code, request, ask=hidden):
    """Авторизация, единственный запрос промо и выход из созданной сессии."""
    auth_attempted = False
    result = None
    try:
        await asyncio.wait_for(client.connect(), 25)
        phone = ask("Номер Telegram с кодом страны (скрытый ввод): ")
        if not re.fullmatch(r"\+[0-9]{7,15}", phone):
            raise ValueError("invalid_phone")
        sent = await asyncio.wait_for(client.send_code_request(phone), 25)
        for attempt in range(3):
            code = ask("Код входа из Telegram (скрытый ввод): ")
            auth_attempted = True
            try:
                await asyncio.wait_for(client.sign_in(
                    phone, code=code, phone_code_hash=sent.phone_code_hash), 25)
                break
            except password_needed:
                password = ask("Пароль двухэтапной проверки (скрытый ввод): ")
                await asyncio.wait_for(client.sign_in(password=password), 25)
                break
            except invalid_code:
                if attempt == 2:
                    raise
                print("Код не принят. Повторите ввод того же кода; новый не запрашивается.")
        response = await asyncio.wait_for(client(request()), 30)
        result = summarize_promo(response, expected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        try:
            if auth_attempted:
                try:
                    logged_out = bool(await asyncio.wait_for(client.log_out(), 12))
                except Exception:
                    logged_out = False
                print("temporary_session_logout=" + str(logged_out).lower())
                if not logged_out:
                    print("Выход не подтверждён. В Telegram → Настройки → Устройства "
                          "завершите сессию Pushok Sponsor Check.")
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), 8)
            except Exception:
                print("disconnect_confirmed=false")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-channel", required=True)
    parser.add_argument("--proxy-env", type=Path,
                        default=Path("/etc/mtproxy-pushok/mtproxy.env"))
    parser.add_argument("--check-only", action="store_true",
                        help="Проверить конфигурацию и библиотеку без сети и входа")
    args = parser.parse_args()
    expected = args.expected_channel.removeprefix("@")
    if not USERNAME.fullmatch(expected):
        parser.error("Некорректный username канала")
    # Журнал SDK может содержать данные RPC. Выводим только свой краткий отчёт.
    logging.disable(logging.CRITICAL)
    try:
        import telethon
        from telethon import TelegramClient, connection, errors, functions
        from telethon.sessions import MemorySession
        if telethon.__version__ != TELETHON_VERSION:
            raise RuntimeError("unexpected_telethon_version")
        proxy = load_proxy(args.proxy_env)
        request = functions.help.GetPromoDataRequest
        print(f"Прокси: {proxy[0]}:{proxy[1]}; ожидаемый канал: @{expected}")
        if args.check_only:
            print("Проверка конфигурации и импорта: OK. Подключения не выполнялись.")
            return 0
        api_id = int(hidden("api_id из my.telegram.org (скрытый ввод): "))
        api_hash = hidden("api_hash из my.telegram.org (скрытый ввод): ")
        if not 0 < api_id < 2**31 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("invalid_api_credentials")

        async def run():
            with warnings.catch_warnings():
                # В SDK 1.45 общее предупреждение SOCKS ошибочно выдаётся и
                # для TcpMTProxy. Этот транспорт сам подключается к proxy[:2]
                # и не использует python-socks; это проверено отдельным тестом.
                warnings.filterwarnings("ignore", category=UserWarning,
                    message="^proxy argument will be ignored because python-socks is not installed$",
                    module=r"telethon\.client\.telegrambaseclient")
                client = TelegramClient(
                    MemorySession(), api_id, api_hash,
                    connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                    # Явный dd-префикс защищает 16-байтный секрет, который сам
                    # начинается с dd/ee, от ошибочного усечения в SDK.
                    proxy=(proxy[0], proxy[1], "dd" + proxy[2]),
                    receive_updates=False, catch_up=False,
                    request_retries=0, connection_retries=1, auto_reconnect=False,
                    flood_sleep_threshold=0, timeout=10,
                    device_model="Pushok Sponsor Check", app_version="0.1",
                    lang_code="ru", system_lang_code="ru",
                )
            return await probe(client, expected, errors.SessionPasswordNeededError,
                               errors.PhoneCodeInvalidError, request)

        asyncio.run(run())
        return 0
    except KeyboardInterrupt:
        print("Проверка прервана.")
        return 130
    except Exception as exc:
        # Не печатаем str(exc), traceback, телефон, код, api_hash или proxy secret.
        print("Проверка не выполнена: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
