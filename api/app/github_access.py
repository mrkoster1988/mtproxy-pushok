"""Read-only GitHub access shared by release checks and the host installer."""
import json
import os
import re
import stat
import urllib.request
from pathlib import Path

REPOSITORY = 'mrkoster1988/mtproxy-pushok'
API = 'https://api.github.com/repos/' + REPOSITORY
FILE = 'github-access-private.json'


def valid_token(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{20,256}', value) is not None


def read_token(directory=None):
    directory = Path(directory or os.getenv('PUSHOK_CONTROL_DIR', '/var/lib/mtproxy-pushok/control'))
    try:
        fd = os.open(directory / FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
                return ''
            raw = stream.read(4097)
        if len(raw) > 4096:
            return ''
        data = json.loads(raw)
        token = data.get('token') if isinstance(data, dict) else None
        return token if valid_token(token) else ''
    except (OSError, ValueError):
        return ''


def request(path, *, user_agent='Pushok-repository', token=None):
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '\r' in path or '\n' in path:
        raise ValueError('invalid_github_path')
    result = urllib.request.Request(API + path, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': user_agent})
    secret = read_token() if token is None else token
    if secret:
        if not valid_token(secret):
            raise ValueError('invalid_github_token')
        # urllib must not forward credentials if GitHub returns a redirect.
        result.add_unredirected_header('Authorization', 'Bearer ' + secret)
    return result
