#!/usr/bin/env python3
"""Передаёт боту только публичную ссылку из локальной конфигурации MTProxy."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.proxy_link import connection_url
from check_telegram_promo import load_proxy


def publish_proxy_link(root=ROOT, env=Path('/etc/mtproxy-pushok/mtproxy.env'), *, instance=None):
    from uuid import UUID
    import time
    if instance is not None and str(UUID(instance)) != instance:
        raise ValueError('invalid_instance')
    target = root / '.control' / ('proxy-link-'+instance+'.json' if instance else 'proxy-link.json')
    name = None
    try:
        host, port, secret = load_proxy(env)
        url = connection_url(host, port, secret)
        fd, name = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), 10001, 10001)
            json.dump({'url': url, **({'expires_at':time.time()+120} if instance else {})}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        return True
    except (OSError, ValueError, KeyError):
        # Удалённая/неверная конфигурация не должна оставлять старую кнопку.
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    finally:
        if name:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == '__main__':
    publish_proxy_link()
