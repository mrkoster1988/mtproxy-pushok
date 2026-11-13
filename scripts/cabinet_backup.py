#!/usr/bin/env python3
"""Приватный комплект кабинета и изолированная проверка восстановления.

Рабочая БД никогда не является целью restore. Команды Docker используют
локальный default context; конфигурация из копии не исполняется.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from uuid import uuid4

from staging_preflight import command_environment, compose_command, read_env
from installer_runtime import sync_directory

WRITERS = {'api', 'bot', 'sbp-worker'}
CONFIG = {'.env': 'config.env', 'compose.yaml': 'compose.yaml',
          'compose.override.yaml': 'compose.override.yaml', 'Caddyfile': 'Caddyfile',
          '.control/payments.json':'payments.json', '.control/emoji-map.json':'emoji-map.json'}
REQUIRED = {'database.dump', 'proofs.tar', 'config.env', 'compose.yaml'}
ALLOWED = REQUIRED | set(CONFIG.values())
LIMIT = 20 * 1024**3
COUNTS_SQL = "SELECT json_build_object('users',(SELECT count(*) FROM users),'campaigns',(SELECT count(*) FROM campaigns),'payment_attempts',(SELECT count(*) FROM payment_attempts));"
PROOFS = '/var/lib/mtproxy-pushok/payment-proofs'
LABEL = 'io.pushok.backup-drill'


class BackupError(RuntimeError):
    """Только сообщения без секретов, содержимого файлов или stderr Docker."""


def environment():
    return dict(command_environment(), DOCKER_CONTEXT='default')


def execute(args, **kwargs):
    kwargs.setdefault('stdout', subprocess.PIPE)
    kwargs.setdefault('stderr', subprocess.DEVNULL)
    kwargs.setdefault('timeout', 600)
    return subprocess.run(args, env=environment(), check=True, **kwargs)


def docker(*args, **kwargs):
    return execute(['docker', *args], **kwargs)


def compose(root, *args, **kwargs):
    return execute(compose_command(root, read_env(root / '.env')) + ['--profile', 'bot', *args], **kwargs)


def private_directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
        raise BackupError('Каталог копии должен принадлежать текущему пользователю, иметь права 0700 и не быть ссылкой.')


@contextmanager
def regular(path, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb', buffering=0) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise BackupError('Ожидается обычный файл размером до 20 ГиБ, без ссылки.')
        if private and (stat.S_IMODE(info.st_mode) not in (0o400, 0o600) or info.st_uid != os.geteuid()):
            raise BackupError('Файлы копии должны принадлежать текущему пользователю и иметь права 0600 или 0400.')
        yield source


@contextmanager
def output(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as destination:
        yield destination
        destination.flush()
        os.fsync(destination.fileno())


def write(path, data):
    with output(path) as target:
        target.write(data)


def digest(path):
    with regular(path, private=True) as source:
        sha = hashlib.sha256()
        size = 0
        for block in iter(lambda: source.read(1024 * 1024), b''):
            sha.update(block)
            size += len(block)
        return {'sha256': sha.hexdigest(), 'size': size}


@contextmanager
def locked(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('В каталоге уже работает установщик или резервное копирование.') from None
        yield
    finally:
        os.close(fd)


def running(root):
    result = compose(root, 'ps', '--status', 'running', '--services', timeout=30)
    return set(result.stdout.decode().splitlines())


@contextmanager
def quiesced(root, allow_downtime):
    active = running(root)
    if active - (WRITERS | {'db', 'caddy'}):
        raise BackupError('В Compose работают дополнительные сервисы: автоматическая остановка поддерживает только стандартный кабинет.')
    if 'db' not in active:
        raise BackupError('PostgreSQL должен быть запущен перед копированием.')
    initial = sorted(active & WRITERS)
    if initial and not allow_downtime:
        raise BackupError('API, бот или worker работают. Для временной остановки нужен --allow-downtime; изменения не выполнены.')
    try:
        if initial:
            compose(root, 'stop', '--timeout', '30', *initial, timeout=120)
        if running(root) & WRITERS:
            raise BackupError('Не удалось остановить все сервисы записи.')
        yield
        if running(root) & WRITERS:
            raise BackupError('Сервисы записи запущены другим процессом: комплект не принят.')
    finally:
        if initial:
            try:
                compose(root, 'start', *initial, timeout=120)
                if not set(initial) <= running(root):
                    raise BackupError('Сервисы не вернулись в состояние running; проверьте docker compose ps.')
            except (OSError, subprocess.SubprocessError):
                raise BackupError('Не удалось возобновить сервисы после копирования; проверьте docker compose ps.') from None


def proof_source(root):
    ids = compose(root, 'ps', '--all', '--quiet', 'api', timeout=30).stdout.decode().split()
    if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', ids[0]):
        raise BackupError('Нужен ровно один существующий контейнер API для определения тома чеков.')
    info = json.loads(docker('inspect', ids[0], timeout=30).stdout)[0]
    project = read_env(root / '.env').get('COMPOSE_PROJECT_NAME', 'mtproxy-pushok')
    labels = info['Config'].get('Labels', {})
    if labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.service') != 'api':
        raise BackupError('Контейнер API не принадлежит ожидаемому проекту.')
    env = dict(item.split('=', 1) for item in info['Config'].get('Env', []) if '=' in item)
    if env.get('PAYMENT_PROOFS_DIR', PROOFS) != PROOFS:
        raise BackupError('Нестандартный путь чеков: используйте отдельную процедуру резервного копирования.')
    mounts = [m for m in info['Mounts'] if m.get('Destination') == PROOFS]
    image = info['Image']
    if len(mounts) != 1 or mounts[0].get('Type') != 'volume':
        raise BackupError('Поддерживается именованный Docker-том чеков стандартной установки.')
    volume = mounts[0].get('Name', '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', volume) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise BackupError('Некорректные метаданные тома или образа API.')
    return volume, image


def counts(root):
    result = compose(root, 'exec', '-T', 'db', 'sh', '-c',
                     'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                     input=COUNTS_SQL.encode(), timeout=30)
    return json.loads(result.stdout)


def proof_members(path, destination=None):
    seen, file_count, total = {}, 0, 0
    with regular(path, private=True) as source, tarfile.open(fileobj=source, mode='r:') as archive:
        for member in archive:
            if len(seen) >= 100000 or len(member.name) > 1024:
                raise BackupError('Архив чеков превышает лимит файлов или длины пути.')
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or '\\' in member.name
                    or len(name.parts) > 32 or not (member.isdir() or member.isfile())):
                raise BackupError('Архив чеков содержит небезопасный путь или тип объекта.')
            key = str(name)
            if key in seen or (key == '.' and not member.isdir()):
                raise BackupError('В архиве чеков повторяется путь или повреждён корень.')
            if any(seen.get(str(parent)) == 'file' for parent in name.parents):
                raise BackupError('Файл используется как родительский каталог в архиве.')
            if member.isfile() and any(existing.startswith(key + '/') for existing in seen):
                raise BackupError('Путь файла конфликтует с каталогом в архиве.')
            seen[key] = 'dir' if member.isdir() else 'file'
            total += member.size
            if member.size < 0 or total > LIMIT:
                raise BackupError('Распакованные чеки превышают 20 ГиБ.')
            target = destination / key if destination is not None else None
            if member.isdir():
                if target is not None:
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            file_count += 1
            extracted = archive.extractfile(member)
            sha = hashlib.sha256()
            copied = 0
            if target is not None:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # extractall не используется: ссылки, устройства и выход из каталога запрещены.
            with extracted as data:
                if target is None:
                    for block in iter(lambda: data.read(1024 * 1024), b''):
                        sha.update(block)
                        copied += len(block)
                else:
                    with output(target) as out:
                        for block in iter(lambda: data.read(1024 * 1024), b''):
                            out.write(block)
                            sha.update(block)
                            copied += len(block)
            if copied != member.size:
                raise BackupError('Архив чеков обрезан.')
            if target is not None and digest(target)['sha256'] != sha.hexdigest():
                raise BackupError('Восстановленный файл не совпадает с архивом.')
    return {'files': file_count, 'bytes': total}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError('Манифест содержит повторяющиеся ключи.')
        result[key] = value
    return result


def verify(bundle):
    private_directory(bundle)
    with regular(bundle / 'manifest.json', private=True) as source:
        raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise BackupError('Манифест слишком большой.')
        manifest = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(manifest, dict) or manifest.get('format') != 'pushok-cabinet-backup-v1':
        raise BackupError('Неизвестный формат резервной копии.')
    if (manifest.get('consistency') != 'compose_writers_stopped'
            or not re.fullmatch(r'[0-9a-f]{40,64}', str(manifest.get('source_commit', '')))
            or manifest.get('release_accepted') is not False):
        raise BackupError('Неверные служебные поля манифеста.')
    files = manifest.get('files')
    if not isinstance(files, dict) or not REQUIRED <= set(files) <= ALLOWED:
        raise BackupError('Неверный состав файлов копии.')
    if {p.name for p in bundle.iterdir()} != set(files) | {'manifest.json'}:
        raise BackupError('В каталоге копии отсутствуют файлы или есть посторонние объекты.')
    for name, expected in files.items():
        if (not isinstance(expected, dict) or type(expected.get('size')) is not int
                or not 0 <= expected['size'] <= LIMIT
                or not re.fullmatch(r'[0-9a-f]{64}', str(expected.get('sha256', '')))
                or digest(bundle / name) != expected):
            raise BackupError('Размер или SHA-256 файла копии не совпадает с манифестом.')
    expected_counts = manifest.get('database_counts')
    if (not isinstance(expected_counts, dict) or set(expected_counts) != {'users', 'campaigns', 'payment_attempts'}
            or any(type(v) is not int or v < 0 for v in expected_counts.values())):
        raise BackupError('В манифесте отсутствуют контрольные количества записей БД.')
    with regular(bundle / 'database.dump', private=True) as dump:
        if dump.read(5) != b'PGDMP':
            raise BackupError('Неверный заголовок PostgreSQL dump.')
    proof = proof_members(bundle / 'proofs.tar')
    if proof != manifest.get('proofs'):
        raise BackupError('Состав чеков не совпадает с манифестом.')
    return manifest


def create(root, *, allow_downtime=False):
    with locked(root):
        read_env(root / '.env')
        volume, image = proof_source(root)
        parent = root / '.backups'
        if parent.exists() or parent.is_symlink():
            private_directory(parent)
        token = uuid4().hex
        incomplete, complete = parent / ('.incomplete-' + token), parent / ('cabinet-' + token)
        try:
            with quiesced(root, allow_downtime):
                private_directory(parent, create=True)
                incomplete.mkdir(mode=0o700)
                original = {}
                for src, dst in CONFIG.items():
                    path = root / src
                    if path.is_symlink():
                        raise BackupError('Файлы конфигурации не должны быть символьными ссылками.')
                    if not path.exists() and src not in ('.env', 'compose.yaml'):
                        continue
                    with regular(path, private=src == '.env') as source:
                        data = source.read(1024 * 1024 + 1)
                        if len(data) > 1024 * 1024:
                            raise BackupError('Файл конфигурации превышает 1 МиБ.')
                        original[src] = data
                    write(incomplete / dst, data)
                with output(incomplete / 'database.dump') as out:
                    compose(root, 'exec', '-T', 'db', 'sh', '-c',
                            'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"', stdout=out)
                with regular(incomplete / 'database.dump', private=True) as dump:
                    compose(root, 'exec', '-T', 'db', 'pg_restore', '--list', stdin=dump, stdout=subprocess.DEVNULL, timeout=60)
                database_counts = counts(root)
                helper_name = 'pushok-backup-read-' + token
                try:
                    with output(incomplete / 'proofs.tar') as out:
                        docker('run', '--rm', '--pull', 'never', '--name', helper_name,
                               '--label', 'io.pushok.backup-read=' + token,
                               '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                               '--security-opt', 'no-new-privileges', '--user', '10001:10001',
                               '--mount', 'type=volume,source=' + volume + ',target=/proofs,readonly',
                               '--entrypoint', 'tar', image, '-cf', '-', '-C', '/proofs', '.', stdout=out)
                finally:
                    cleanup_owned(helper_name, 'io.pushok.backup-read', token)
                proof = proof_members(incomplete / 'proofs.tar')
                for src, data in original.items():
                    with regular(root / src, private=src == '.env') as current:
                        if current.read(1024 * 1024 + 1) != data:
                            raise BackupError('Конфигурация изменилась во время копирования; комплект не принят.')
                revision = execute(['git', '-C', str(root), 'rev-parse', 'HEAD'], timeout=10).stdout.decode().strip()
                if not re.fullmatch(r'[0-9a-f]{40,64}', revision):
                    raise BackupError('Не удалось определить коммит исходного кода.')
                manifest = {'format': 'pushok-cabinet-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                            'source_commit': revision, 'consistency': 'compose_writers_stopped',
                            'files': {p.name: digest(p) for p in sorted(incomplete.iterdir())},
                            'proofs': proof, 'database_counts': database_counts,
                            'release_accepted': False}
                write(incomplete / 'manifest.json', json.dumps(manifest, indent=2).encode())
                verify(incomplete)
                sync_directory(incomplete)
            # Публикуем комплект только после возвращения исходных сервисов.
            incomplete.rename(complete)
            sync_directory(parent)
            return complete
        finally:
            if incomplete.exists():
                shutil.rmtree(incomplete)


def drill(bundle):
    manifest = verify(bundle)
    token = uuid4().hex
    name = 'pushok-restore-' + token
    container = None
    with tempfile.TemporaryDirectory(prefix='pushok-restore-files-') as temporary:
        proof = proof_members(bundle / 'proofs.tar', Path(temporary))
        try:
            image = json.loads(docker('image', 'inspect', 'postgres:16-alpine', timeout=30).stdout)[0]['Id']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
                raise BackupError('Нужен локальный образ postgres:16-alpine.')
            container = docker('run', '--detach', '--rm', '--pull', 'never', '--name', name,
                               '--label', LABEL + '=' + token, '--network', 'none', '--memory', '1g', '--cpus', '1',
                               '--pids-limit', '128', '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
                               '--env', 'POSTGRES_DB=pushok_restore', image, timeout=60).stdout.decode().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', container):
                raise BackupError('Docker не вернул ID тестового контейнера.')
            ready = False
            for _ in range(45):
                try:
                    docker('exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'pushok_restore', timeout=5)
                    ready = True
                    break
                except subprocess.CalledProcessError:
                    time.sleep(1)
            if not ready:
                raise BackupError('Изолированная PostgreSQL не запустилась.')
            with regular(bundle / 'database.dump', private=True) as dump:
                docker('exec', '-i', container, 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
                       '-U', 'postgres', '-d', 'pushok_restore', stdin=dump, stdout=subprocess.DEVNULL)
            result = docker('exec', '-i', container, 'psql', '-X', '-At', '-v', 'ON_ERROR_STOP=1',
                            '-U', 'postgres', '-d', 'pushok_restore', input=COUNTS_SQL.encode(), timeout=30)
            if json.loads(result.stdout) != manifest['database_counts']:
                raise BackupError('Контрольные количества записей после restore не совпали.')
            return {'ok': True, 'database_restored': True, 'proofs_restored': proof,
                    'configuration_executed': False, 'release_accepted': False}
        finally:
            cleanup_owned(name, LABEL, token)


def cleanup_owned(name, label, token):
    # Проверяем имя и метку; удаляем только свой контейнер и его anonymous volumes.
    result = docker('ps', '--all', '--quiet', '--no-trunc', '--filter', 'name=^/' + name + '$', timeout=30)
    ids = result.stdout.decode().split()
    if not ids:
        return
    if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{64}', ids[0]):
        raise BackupError('Не удалось однозначно определить временный контейнер для очистки.')
    info = json.loads(docker('inspect', ids[0], timeout=30).stdout)[0]
    if info.get('Name') != '/' + name or info['Config'].get('Labels', {}).get(label) != token or info['Id'] != ids[0]:
        raise BackupError('Временный контейнер не удалён: имя/метка принадлежности не совпали.')
    docker('rm', '--force', '--volumes', ids[0], timeout=60)


def menu():
    print('MTProxy Pushok — резервная копия кабинета')
    print('1) Показать план\n2) Создать копию\n3) Проверить комплект\n4) Пробное восстановление\n5) Выход')
    choice = input('Выберите номер [1]: ').strip() or '1'
    if choice == '5':
        return None
    if choice not in ('1', '2', '3', '4'):
        raise BackupError('Введите номер из списка.')
    if choice == '2':
        print('Кабинет, бот и worker будут временно недоступны; после копирования возобновятся только ранее работавшие сервисы.')
        answer = input('1) Только план [по умолчанию]; 2) Создать с остановкой; 3) Отмена: ').strip() or '1'
        return ('create', None, True) if answer == '2' else ('plan', None, False) if answer == '1' else None
    if choice in ('3', '4'):
        path = Path(input('Каталог cabinet-* с manifest.json: ').strip())
        return ('verify' if choice == '3' else 'drill', path, False)
    return ('plan', None, False)


def main():
    parser = argparse.ArgumentParser(description='Резервная копия кабинета MTProxy Pushok')
    parser.add_argument('action', nargs='?', choices=('plan', 'create', 'verify', 'drill'))
    parser.add_argument('bundle', nargs='?', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--allow-downtime', action='store_true')
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise BackupError('Операция прервана. Проверьте состояние сервисов и незавершённые копии.')
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        if args.action is None:
            selected = menu()
            if selected is None:
                return 0
            args.action, args.bundle, args.allow_downtime = selected
        if args.action == 'plan':
            print('План: блокировка → остановка api/bot/worker с разрешением → PostgreSQL + чеки + конфигурация → SHA-256 → возобновление сервисов.')
            print('Пробное восстановление использует новую PostgreSQL без сети и временный каталог файлов. Рабочая БД не является целью restore.')
            return 0
        if args.action in ('verify', 'drill') and args.bundle is None:
            raise BackupError('Укажите каталог комплекта cabinet-*.')
        if args.action == 'create':
            path = create(args.root.resolve(), allow_downtime=args.allow_downtime)
            result = {'ok': True, 'bundle': str(path), 'release_accepted': False}
        elif args.action == 'verify':
            manifest = verify(args.bundle.absolute())
            result = {'ok': True, 'integrity_verified': True, 'database_restored': False,
                      'proofs': manifest['proofs'], 'release_accepted': False}
        else:
            result = drill(args.bundle.absolute())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BackupError as error:
        message = str(error)
    except KeyboardInterrupt:
        message = 'Операция отменена. Проверьте состояние сервисов.' 
    except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError, tarfile.TarError, subprocess.SubprocessError):
        message = 'Операция не завершена. Проверьте права, место, Git, Docker и локальный образ postgres:16-alpine. Содержимое файлов и stderr скрыты.'
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps({'ok': False, 'error': message, 'release_accepted': False}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
