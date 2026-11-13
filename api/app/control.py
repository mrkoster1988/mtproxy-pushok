"""Приватные настройки оплаты и очередь обновления. Без Docker socket и shell в API."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


def root():
    return Path(os.getenv('PUSHOK_CONTROL_DIR', '/var/lib/mtproxy-pushok/control'))


def read(name, default=None):
    try:
        return json.loads((root() / name).read_text())
    except (OSError, ValueError):
        return default


def write(name, data):
    directory = root()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
        os.replace(path, directory / name)
    finally:
        Path(path).unlink(missing_ok=True)


@contextmanager
def locked():
    root().mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root() / 'lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def flag(name, fallback):
    return read('payments.json', {}).get(name, fallback) is True


def now():
    return datetime.now(timezone.utc).isoformat()
