#!/usr/bin/env python3
"""Configure read-only access locally on the VPS, without tokens in arguments."""
import argparse
import getpass
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
from app import github_access


def verify(token):
    # Contents and Actions are separate fine-grained token permissions.
    for path in ('/git/ref/heads/release/stable', '/actions/runs?per_page=1'):
        with urllib.request.urlopen(github_access.request(path, token=token), timeout=10) as response:
            if response.status != 200:
                raise ValueError('repository_access_unavailable')


def save(root, token):
    directory = root / '.control'
    if directory.is_symlink():
        raise ValueError('control_symlink')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, 10001, 10001)
    os.chmod(directory, 0o700)
    fd, name = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchown(stream.fileno(), 10001, 10001)
            os.fchmod(stream.fileno(), 0o600)
            json.dump({'token': token}, stream)
        os.replace(name, directory / github_access.FILE)
    finally:
        Path(name).unlink(missing_ok=True)
    # An empty helper resets inherited helpers for this repository URL only.
    prefix = 'credential.https://github.com/' + github_access.REPOSITORY + '.git.'
    helper = '!python3 ' + shlex.quote(str(root / 'scripts/github_credentials.py'))
    for arguments in (('--replace-all', prefix + 'helper', ''),
                      ('--add', prefix + 'helper', helper),
                      (prefix + 'useHttpPath', 'true')):
        subprocess.run(['git', '-C', str(root), 'config', '--local', *arguments], check=True)


def main():
    parser = argparse.ArgumentParser(description='Доступ VPS к закрытому репозиторию Pushok')
    parser.add_argument('--check', action='store_true', help='Проверить сохранённый доступ, не меняя настройки')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('Запустите от root: sudo bash setup.sh --repository-access', file=sys.stderr)
        return 1
    try:
        if args.check:
            token = github_access.read_token(ROOT / '.control')
            if not token:
                raise ValueError('repository_access_missing')
        else:
            print('GitHub: токен только для mtproxy-pushok; Contents и Actions — Read-only.')
            print('Токен вводится скрыто и сохраняется на VPS с правами 0600.')
            with warnings.catch_warnings():
                # Never fall back to echoed stdin when no secure terminal exists.
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('GitHub token (скрыт): ')
            if not github_access.valid_token(token):
                raise ValueError('invalid_token')
        verify(token)
        if not args.check:
            save(ROOT, token)
        print('Доступ к выпускам и проверкам GitHub подтверждён.'
              + ('' if args.check else ' Установка и обновления готовы к закрытому репозиторию.'))
        return 0
    except (OSError, ValueError, EOFError, getpass.GetPassWarning, subprocess.SubprocessError):
        print('Не удалось настроить доступ. Проверьте токен, срок действия, доступ к репозиторию '
              'и права Contents/Actions: Read-only. Токен не выводится.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
