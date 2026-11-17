#!/usr/bin/env python3
"""Постоянное подключение владельца MTProxy: вход, состояние и отзыв сессии на VPS."""
import argparse
import asyncio
import fcntl
import getpass
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import uuid
import warnings

TELETHON_VERSION = "1.45.0"
DEVICE_NAME = "Pushok MTProxy Owner"
DEFAULT_STATE = Path("/var/lib/mtproxy-pushok/telegram-owner")
ROOT = Path(__file__).resolve().parents[1]


class OwnerError(Exception):
    pass


class PrivateState:
    """Файл авторизации вне checkout, приватный каталог и блокировка одного процесса."""
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.directory = self.lock = None

    @staticmethod
    def validate(fd, *, directory=False):
        info = os.fstat(fd)
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not correct_type or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                or (not directory and info.st_nlink != 1)):
            raise OwnerError("unsafe_state_permissions")

    def __enter__(self):
        if self.path.resolve().is_relative_to(ROOT):
            raise OwnerError("state_inside_repository")
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.validate(self.directory, directory=True)
            self.lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                0o600, dir_fd=self.directory)
            self.validate(self.lock)
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OwnerError("connection_busy") from None
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for fd in (self.lock, self.directory):
            if fd is not None:
                os.close(fd)
        self.lock = self.directory = None

    def read(self):
        try:
            fd = os.open("owner.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self.directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, encoding="utf-8") as stream:
            self.validate(stream.fileno())
            if os.fstat(stream.fileno()).st_size > 16384:
                raise OwnerError("invalid_state")
            data = json.load(stream)
        if (not isinstance(data, dict) or set(data) != {"api_id", "api_hash", "session", "user_id"}
                or type(data["user_id"]) is not int or data["user_id"] <= 0
                or not isinstance(data["session"], str) or not 1 <= len(data["session"]) <= 2048):
            raise OwnerError("invalid_state")
        validate_credentials(data["api_id"], data["api_hash"])
        return data

    def save(self, data):
        temp = ".owner-" + uuid.uuid4().hex
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, "owner.json", src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        finally:
            try:
                os.unlink(temp, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def remove(self):
        os.unlink("owner.json", dir_fd=self.directory)
        os.fsync(self.directory)


def hidden(prompt):
    # Пароль 2FA может содержать пробелы; не меняем его strip().
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def validate_credentials(api_id, api_hash):
    if (type(api_id) is not int or not 0 < api_id < 2**31 or not isinstance(api_hash, str)
            or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash)):
        raise OwnerError("invalid_api_credentials")


async def call(request):
    return await asyncio.wait_for(request, 25)


async def authorize(client, errors, ask):
    phone = ask("Номер аккаунта владельца тегов, с кодом страны (скрыто): ").strip()
    if not re.fullmatch(r"\+[0-9]{7,15}", phone):
        raise OwnerError("invalid_phone")
    sent = await call(client.send_code_request(phone))
    for attempt in range(3):
        code = ask("Код входа из Telegram (скрыто): ").strip()
        if not re.fullmatch(r"[0-9]{4,8}", code):
            if attempt == 2:
                raise OwnerError("invalid_code")
            print("Введите только цифры кода.")
            continue
        try:
            await call(client.sign_in(phone, code=code, phone_code_hash=sent.phone_code_hash))
            return
        except errors.SessionPasswordNeededError:
            for password_attempt in range(3):
                try:
                    await call(client.sign_in(password=ask("Пароль двухэтапной проверки (скрыто): ")))
                    return
                except errors.PasswordHashInvalidError:
                    if password_attempt == 2:
                        raise
                    print("Пароль не принят. Повторите ввод.")
        except errors.PhoneCodeInvalidError:
            if attempt == 2:
                raise
            print("Код не принят. Повторите ввод; новый код не запрашивается.")


async def operate(client, action, state, credentials, existing, errors, ask=hidden):
    new_login = False
    persisted = False
    try:
        await call(client.connect())
        # is_user_authorized() в SDK скрывает любой RPCError как False, включая
        # временный сбой. get_me() возвращает None только при UnauthorizedError.
        me = await call(client.get_me())
        authorized = me is not None
        if action == "disconnect":
            if authorized and not await call(client.log_out()):
                raise OwnerError("logout_not_confirmed")
            state.remove()
            return {"state": "disconnected", "automatic_binding_enabled": False}
        if existing and not authorized:
            return {"state": "revoked", "automatic_binding_enabled": False}
        if not authorized:
            new_login = True
            await authorize(client, errors, ask)
            me = await call(client.get_me())
        if me is None or getattr(me, "bot", False) or type(me.id) is not int or me.id <= 0:
            raise OwnerError("user_account_required")
        if existing and me.id != existing["user_id"]:
            raise OwnerError("account_mismatch")
        bot = await call(client.get_entity("MTProxyBot"))
        if not getattr(bot, "bot", False) or str(getattr(bot, "username", "")).lower() != "mtproxybot":
            raise OwnerError("unexpected_mtproxy_bot")
        # StringSession хранится только в приватном файле. Никогда не выводим её.
        state.save(dict(credentials, user_id=me.id, session=client.session.save()))
        persisted = True
        return {"state": "connected", "telegram_user_id": me.id,
                "username": getattr(me, "username", None), "mtproxy_bot_available": True,
                "tag_ownership_verified": False, "automatic_binding_enabled": False}
    finally:
        try:
            if new_login and not persisted:
                try:
                    revoked = bool(await asyncio.wait_for(client.log_out(), 12))
                except Exception:
                    revoked = False
                if not revoked:
                    print("Вход не завершён. В Telegram → Настройки → Устройства завершите сессию " + DEVICE_NAME + ".")
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), 8)
            except Exception:
                print("Соединение не подтвердило закрытие. Состояние авторизации не изменялось.")


MESSAGES = {
    "unsafe_state_permissions": "Каталог авторизации должен принадлежать текущему пользователю и иметь права 0700, файлы — 0600.",
    "state_inside_repository": "Каталог авторизации должен находиться вне исходного кода проекта.",
    "connection_busy": "Другой процесс уже работает с подключением. Дождитесь его завершения.",
    "invalid_state": "Файл авторизации повреждён. Сначала завершите сессию Pushok MTProxy Owner в Telegram.",
    "invalid_api_credentials": "Проверьте api_id и api_hash с my.telegram.org/apps.",
    "invalid_phone": "Номер должен начинаться с + и содержать код страны и цифры.",
    "invalid_code": "Код входа должен содержать от 4 до 8 цифр.",
    "logout_not_confirmed": "Telegram не подтвердил отзыв сессии. Файл сохранён для повторного отключения.",
    "user_account_required": "Нужен пользовательский аккаунт владельца тегов, не токен бота.",
    "account_mismatch": "Аккаунт не совпал с сохранённым подключением.",
    "unexpected_mtproxy_bot": "Не удалось подтвердить адрес @MTProxyBot.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("connect", "enable", "disable", "status", "disconnect", "check"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    try:
        if args.action in ('connect','enable') and (not sys.stdin.isatty() or not sys.stdout.isatty()):
            print("Подключение запускается только в интерактивном терминале VPS.")
            return 2
        import telethon
        from telethon import TelegramClient, errors
        from telethon.sessions import StringSession
        if telethon.__version__ != TELETHON_VERSION:
            raise OwnerError("sdk_version")
        with PrivateState(args.state_dir) as state:
            existing = state.read()
            if args.action in ('disable','disconnect'):
                try:
                    os.unlink('automatic.enabled', dir_fd=state.directory)
                    os.fsync(state.directory)
                except FileNotFoundError:
                    pass
                if args.action == 'disable':
                    print('{"automatic_binding_enabled": false}')
                    return 0
            if args.action == "check":
                print(json.dumps({"state": "session_saved" if existing else "not_connected",
                                  "sdk_version": TELETHON_VERSION, "network_checked": False}, ensure_ascii=False))
                return 0
            if not existing and args.action not in ('connect','enable'):
                print(json.dumps({"state": "not_connected", "automatic_binding_enabled": False}))
                return 0
            if existing:
                credentials = {key: existing[key] for key in ("api_id", "api_hash")}
            else:
                print("Подключаем аккаунт, в котором зарегистрированы прокси в @MTProxyBot.")
                print("Используется Telegram API. Сессия будет сохранена на этом VPS; код и пароль не сохраняются.")
                print("Это сессия вашего аккаунта. Она появится в Telegram → Настройки → Устройства как " + DEVICE_NAME + ".")
                credentials = {"api_id": int(hidden("api_id с my.telegram.org/apps (скрыто): ").strip()),
                               "api_hash": hidden("api_hash с my.telegram.org/apps (скрыто): ").strip()}
                validate_credentials(**credentials)

            async def run():
                client = TelegramClient(
                    StringSession(existing["session"] if existing else None), **credentials,
                    device_model=DEVICE_NAME, app_version="0.1", lang_code="ru", system_lang_code="ru",
                    receive_updates=False, catch_up=False, request_retries=0,
                    connection_retries=1, auto_reconnect=False, flood_sleep_threshold=0, timeout=10)
                return await operate(client, args.action, state, credentials, existing, errors)

            result = asyncio.run(run())
            if args.action == 'enable' and result['state'] == 'connected':
                fd = os.open('automatic.enabled', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=state.directory)
                try:
                    state.validate(fd)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(state.directory)
            result['automatic_binding_enabled'] = (state.path / 'automatic.enabled').is_file()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result["state"] == "connected":
                print("Аккаунт подключён. " + ('Агент будет готовить привязки через @MTProxyBot.' if result['automatic_binding_enabled']
                      else 'Для автоматической привязки выполните connect_telegram_owner.sh enable.'))
            elif result["state"] == "revoked":
                print("Сессия отозвана в Telegram. Выполните disconnect, затем connect для повторного входа.")
                return 1
        return 0
    except KeyboardInterrupt:
        print("Подключение прервано.")
        return 130
    except Exception as exc:
        if isinstance(exc, OwnerError):
            print(MESSAGES.get(str(exc), "Нужна библиотека Telethon==" + TELETHON_VERSION + "."))
        else:
            # RPC-текст может содержать данные пользователя; показываем только тип.
            print("Операция не выполнена: " + type(exc).__name__)
            if type(exc).__name__ in ("FloodWaitError", "PhoneNumberFloodError"):
                print("Telegram ограничил попытки. Подождите, не запрашивайте код многократно.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
