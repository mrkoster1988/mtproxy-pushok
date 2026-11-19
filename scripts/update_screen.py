#!/usr/bin/env python3
"""Подготовка публичного экрана обновления для Caddy установщика."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGES = {'preflight', 'backup', 'source', 'build', 'migration', 'restart', 'health', 'proxy'}
MOUNT = '      - ./.maintenance:/srv/pushok-update:ro\n'
CADDY_MOUNT = '      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n'

# Только точные пути публичных файлов. Запросы к API продолжают получать штатные ответы.
ROUTES = """    @updateAssets path /update/screen.html /update/screen.css /update/screen.js /update/pushok.svg /update/status.json
    handle @updateAssets {
        uri strip_prefix /update
        root * /srv/pushok-update
        header Cache-Control "no-store"
        file_server
    }
    @updating {
        path / /miniapp /miniapp/
        file {
            root /srv/pushok-update
            try_files /running
        }
    }
    handle @updating {
        rewrite * /screen.html
        root * /srv/pushok-update
        header Cache-Control "no-store"
        file_server
    }
    handle {
        reverse_proxy api:8000
    }
    @cabinet path / /miniapp /miniapp/
    handle_errors 502 503 504 {
        handle @cabinet {
            rewrite * /screen.html
            root * /srv/pushok-update
            header Cache-Control "no-store"
            file_server {
                status 200
            }
        }
    }
"""


def write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf-8') as stream:
        temp = Path(stream.name)
        try:
            stream.write(content)
            os.fchmod(stream.fileno(), 0o644)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def publish(root, state, stage=None):
    directory = root / '.maintenance'
    if not directory.is_dir():
        return
    data = {'state': state}
    if state == 'running' and isinstance(stage, str) and stage in STAGES:
        data['stage'] = stage
    write(directory / 'status.json', json.dumps(data))
    marker = directory / 'running'
    if state == 'running':
        write(marker, '')
    else:
        marker.unlink(missing_ok=True)


def prepare(root):
    directory = root / '.maintenance'
    directory.mkdir(mode=0o755, exist_ok=True)
    directory.chmod(0o755)
    for name in ('screen.html', 'screen.css', 'screen.js', 'pushok.svg'):
        write(directory / name, (root / 'api' / 'web' / 'update' / name).read_text())
    if not (directory / 'status.json').exists():
        publish(root, 'idle')
    config, compose = root / 'Caddyfile', root / 'compose.override.yaml'
    if config.is_file() and compose.is_file():
        original, override = config.read_text(), compose.read_text()
        site = re.fullmatch(r'([A-Za-z0-9.-]+) \{\n reverse_proxy api:8000\n\}\n', original)
        if site and override.count(CADDY_MOUNT) == 1:
            if MOUNT not in override:
                write(compose, override.replace(CADDY_MOUNT, CADDY_MOUNT + MOUNT))
            write(config, site[1] + ' {\n' + ROUTES + '}\n')


if __name__ == '__main__':
    if sys.argv[1:] == ['--routes']:
        print(ROUTES, end='')
    else:
        prepare(ROOT)
