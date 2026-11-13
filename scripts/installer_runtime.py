#!/usr/bin/env python3
"""Операции мастера после подтверждения: конфигурация, backup, миграции, health.

Секреты приходят из приватного .env или stdin, никогда из аргументов CLI.
Резервная копия содержит только PostgreSQL, не том чеков или конфигурацию.
"""
import argparse
import base64
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from staging_preflight import (ConfigError, command_environment, compose_command,
                               parse_env, read_env, validate_values)


class InstallError(RuntimeError):
    """Сообщения фиксированы, без вывода секретов и ответов сервисов."""


def validate(text):
    values = parse_env(text)
    failed = []
    validate_values(values, lambda name, ok, details: failed.append(name) if not ok else None,
                    allow_telegram_deferred=True)
    if failed:
        raise InstallError('Конфигурация не прошла проверки: ' + ', '.join(failed))
    return values


def sync_directory(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_env(root, text, *, replace=False, expected_stat=None):
    validate(text)
    destination = root / '.env'
    before = None
    if replace:
        read_env(destination)
        before = destination.lstat()
        if expected_stat is not None and before != expected_stat:
            raise InstallError('.env изменён другим процессом. Повторите мастер.')
    fd, name = tempfile.mkstemp(prefix='.env.install-', dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            now = destination.lstat()
            if (now.st_dev, now.st_ino, now.st_mtime_ns, now.st_size) != (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size):
                raise InstallError('.env изменён другим процессом. Повторите мастер.')
            os.replace(temporary, destination)
        else:
            # link() не перезаписывает файл, появившийся после диалога.
            os.link(temporary, destination)
        sync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def set_payment(root, encoded):
    if len(encoded) > 22000:
        raise InstallError('Инструкция оплаты слишком длинная.')
    try:
        decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        raise InstallError('Инструкция оплаты должна быть UTF-8 в base64.') from None
    if len(decoded) > 4000 or any(c in decoded for c in '\r\n\x00'):
        raise InstallError('Инструкция оплаты: одна строка, до 4000 символов.')
    read_env(root / '.env')
    before = (root / '.env').lstat()
    text = (root / '.env').read_text(encoding='utf-8')
    lines = [line for line in text.splitlines() if not re.match(r'^\s*PAYMENT_INSTRUCTIONS_B64\s*=', line)]
    atomic_env(root, '\n'.join(lines) + '\nPAYMENT_INSTRUCTIONS_B64=' + encoded + '\n', replace=True, expected_stat=before)


def command(root, args):
    return compose_command(root, read_env(root / '.env')) + list(args)


def run(root, args, **kwargs):
    return subprocess.run(command(root, args), cwd=root, env=command_environment(), check=True, **kwargs)


def backup_database(root):
    directory = root / '.backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()):
        raise InstallError('.backups должен быть каталогом владельца с правами 0700, без символьной ссылки.')
    fd, name = tempfile.mkstemp(prefix='pre-migration-', suffix='.dump', dir=directory)
    destination = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            run(root, ['exec', '-T', 'db', 'sh', '-c',
                       'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                stdout=output, stderr=subprocess.DEVNULL, timeout=300)
            output.flush()
            os.fsync(output.fileno())
        with destination.open('rb') as dump:
            if dump.read(5) != b'PGDMP':
                raise InstallError('PostgreSQL вернул неполную резервную копию.')
            dump.seek(0)
            run(root, ['exec', '-T', 'db', 'pg_restore', '--list'], stdin=dump,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        sync_directory(directory)
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def migration_sql(root, names):
    expected = sorted(path.relative_to(root).as_posix() for path in (root / 'database').glob('[0-9][0-9][0-9]_*.sql')
                      if not path.name.startswith('001_'))
    if list(names) != expected or not expected:
        raise InstallError('Список миграций мастера не совпадает с файлами database/002+; обновите полный репозиторий.')
    prefix = "SET LOCAL lock_timeout = '10s';\nSET LOCAL statement_timeout = '60s';\n"
    return (prefix + '\n'.join((root / name).read_text(encoding='utf-8') for name in names) + '\n').encode()


def migrate(root, names):
    data = migration_sql(root, names)
    run(root, ['exec', '-T', 'db', 'sh', '-c',
               'exec psql -X --single-transaction -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --file=-'],
        input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def wait_api(root, timeout=120):
    values = read_env(root / '.env')
    port = values.get('API_BIND_PORT', '8000')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1024 <= int(port) <= 65535:
        raise InstallError('Неверный локальный порт API.')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open('http://127.0.0.1:' + port + '/health/ready',
                             timeout=max(0.01, min(3, deadline - time.monotonic()))) as response:
                raw = response.read(65537)
                if response.status == 200 and len(raw) <= 65536:
                    body = json.loads(raw)
                    if isinstance(body, dict) and body.get('status') == 'ready' and body.get('database') == 'ok':
                        return True
        except (OSError, URLError, ValueError, http.client.HTTPException):
            pass
        time.sleep(max(0, min(2, deadline - time.monotonic())))
    return False


def main():
    parser = argparse.ArgumentParser(description='Внутренние операции мастера MTProxy Pushok')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('action', choices=('write-env', 'set-payment', 'compose', 'backup', 'migrate', 'wait-api', 'port'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.action in ('write-env', 'set-payment'):
            text = sys.stdin.read(65537)
            if len(text) > 65536:
                raise InstallError('Ввод превышает допустимый размер.')
            if args.action == 'write-env':
                atomic_env(root, text)
            else:
                set_payment(root, text.rstrip('\n'))
        elif args.action == 'compose':
            return run(root, args.arguments).returncode
        elif args.action == 'backup':
            print('Копия PostgreSQL перед миграциями: ' + backup_database(root).name)
            print('Каталог .backups приватный. Проверка списка архива не заменяет пробное восстановление.')
        elif args.action == 'migrate':
            migrate(root, args.arguments)
            print('Миграции 002+ завершены одной транзакцией.')
        elif args.action == 'wait-api':
            if not wait_api(root):
                raise InstallError('API/БД не подтвердили готовность за 120 секунд. Проверьте сервисы; установка не объявлена успешной.')
        elif args.action == 'port':
            port = read_env(root / '.env').get('API_BIND_PORT', '8000')
            if not re.fullmatch(r'[0-9]{1,5}', port) or not 1024 <= int(port) <= 65535:
                raise InstallError('Неверный порт API.')
            print(port)
    except (InstallError, ConfigError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Операция мастера не завершена. Проверьте права, свободное место и состояние сервисов. Диагностика скрыта для защиты секретов.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
