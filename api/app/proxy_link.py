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


def read_json(path, *, limit=8192):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('invalid_link_file')
        return json.loads(stream.read(limit+1))


def validated_url(raw):
    if not isinstance(raw, str):
        return None
    parts = urlsplit(raw)
    if parts.scheme != 'https' or parts.netloc != 't.me' or parts.path != '/proxy' or parts.fragment:
        return None
    query = parse_qs(parts.query, strict_parsing=True)
    if set(query) != {'server','port','secret'} or any(len(v) != 1 for v in query.values()):
        return None
    return connection_url(query['server'][0],int(query['port'][0]),query['secret'][0])


def is_live(data):
    import time
    expiry = data.get('expires_at') if isinstance(data,dict) else None
    now = time.time()
    return type(expiry) in (int,float) and now < expiry <= now+125


def read_proxy_url(path, *, live=False):
    try:
        data = read_json(path,limit=2048)
        if not isinstance(data,dict) or live and not is_live(data):
            return None
        return validated_url(data.get('url'))
    except (OSError,ValueError,TypeError):
        return None


def current_gateway_url(topic=None):
    try:
        data = read_json(LINK_FILE.parent/'proxy-gateway.json')
        if not is_live(data):
            return None
        url = validated_url(data.get('url'))
        if not url or topic is None:
            return url
        port = data.get('topic_ports',{}).get(topic)
        if type(port) is not int or not 1024 <= port <= 65535:
            return None
        query = parse_qs(urlsplit(url).query)
        return connection_url(query['server'][0],port,query['secret'][0])
    except (OSError,ValueError,TypeError,AttributeError):
        return None


def current_proxy_url():
    """Новые подключения распределяются по готовым экземплярам; старые не разрываются."""
    import secrets
    gateway = current_gateway_url()
    if gateway:
        return gateway
    choices = [read_proxy_url(LINK_FILE)]
    for path in sorted(LINK_FILE.parent.glob('proxy-link-*.json'))[:3]:
        if re.fullmatch(r'proxy-link-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.json',path.name):
            choices.append(read_proxy_url(path,live=True))
    choices = [url for url in choices if url]
    return secrets.choice(choices) if choices else None
