#!/usr/bin/env python3
"""Git credential helper restricted to the Pushok repository; never stores input."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
from app.github_access import REPOSITORY, read_token


def credentials(operation, raw, directory):
    if operation != 'get' or len(raw) > 4096:
        return ''
    fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    if fields.get('protocol') != 'https' or fields.get('host') != 'github.com':
        return ''
    if fields.get('path') not in (REPOSITORY, REPOSITORY + '.git'):
        return ''
    token = read_token(directory)
    return 'username=x-access-token\npassword=' + token + '\n\n' if token else ''


if __name__ == '__main__':
    # stdout is the Git credential protocol, not a user-facing diagnostic.
    sys.stdout.write(credentials(sys.argv[1] if len(sys.argv) == 2 else '',
                                 sys.stdin.read(4097), ROOT / '.control'))
