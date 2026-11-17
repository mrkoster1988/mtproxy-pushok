"""Публичная ссылка подключения; административная конфигурация боту не передаётся."""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qs, urlencode, urlsplit

LINK_FILE = Path('/var/lib/mtproxy-pushok/control/proxy-link.json')


def connection_url(host, port, secret):
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid_proxy_endpoint')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (len(host) > 253 or '.' not in host or not all(
                re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                for label in host.split('.'))):
            raise ValueError('invalid_proxy_host') from None
    else:
        if not address.is_global:
            raise ValueError('invalid_proxy_host')
    if not isinstance(secret, str) or not re.fullmatch(r'(?:dd)?[0-9a-fA-F]{32}', secret):
        raise ValueError('invalid_proxy_secret')
    secret = secret.lower()
    if len(secret) == 32:
        secret = 'dd' + secret
    return 'https://t.me/proxy?' + urlencode({'server': host, 'port': port, 'secret': secret})


def read_proxy_url(path, *, live=False):
    """Перечитываем файл: замена адреса/секрета не требует перезапуска бота."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2048:
                return None
            data = json.loads(stream.read(2049))
        raw = data.get('url') if isinstance(data, dict) else None
        if live:
            import time
            expiry = data.get('expires_at') if isinstance(data,dict) else None
            if type(expiry) not in (int,float) or not time.time() < expiry <= time.time()+125:
                return None
        if not isinstance(raw, str):
            return None
        parts = urlsplit(raw)
        if (parts.scheme != 'https' or parts.netloc != 't.me' or parts.path != '/proxy'
                or parts.fragment):
            return None
        query = parse_qs(parts.query, strict_parsing=True)
        if set(query) != {'server', 'port', 'secret'} or any(len(v) != 1 for v in query.values()):
            return None
        return connection_url(query['server'][0], int(query['port'][0]), query['secret'][0])
    except (OSError, ValueError, TypeError):
        return None


def current_proxy_url():
    """Новые подключения распределяются по готовым экземплярам; старые не разрываются."""
    import secrets
    choices = [read_proxy_url(LINK_FILE)]
    for path in sorted(LINK_FILE.parent.glob('proxy-link-*.json'))[:3]:
        if re.fullmatch(r'proxy-link-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.json',path.name):
            choices.append(read_proxy_url(path,live=True))
    choices = [url for url in choices if url]
    return secrets.choice(choices) if choices else None
