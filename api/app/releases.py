"""Read-only discovery of the published release; never installs or exposes credentials."""
import base64
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from threading import Lock
from . import github_access

REPOSITORY = 'https://api.github.com/repos/mrkoster1988/mtproxy-pushok'
_lock = Lock()
_cache = {}


def version_key(value):
    # v1.01 -> v1.02 -> v2.00. Legacy 0.066 remains comparable.
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}', value):
        raise ValueError('invalid_version')
    return tuple(map(int, value.split('.')))


def readme_version(readme):
    versions = re.findall(r'(?m)^Версия проекта:\s*v(\d+\.\d+)\.$', readme)
    if len(versions) != 1:
        raise ValueError('invalid_release')
    version_key(versions[0])
    return versions[0]


def _json(path):
    request = github_access.request(path, user_agent='Pushok-release-check')
    with urllib.request.urlopen(request, timeout=3) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('response_too_large')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('invalid_response')
    return data


def check(installed_version):
    # Shared for all administrators, including manual clicks; bound GitHub requests.
    with _lock:
        if not _cache or time.monotonic() - _cache['time'] >= 60:
            previous = _cache.get('result', {})
            result = {key: previous.get(key) for key in ('available_version', 'commit', 'checked_at')}
            result.update(state='unavailable', attempted_at=datetime.now(timezone.utc).isoformat())
            try:
                ref = _json('/git/ref/heads/release/stable')
                obj = ref.get('object', {})
                sha = obj.get('sha')
                if obj.get('type') != 'commit' or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
                    raise ValueError('invalid_commit')
                document = _json('/contents/README.md?ref=' + sha)
                if document.get('encoding') != 'base64':
                    raise ValueError('invalid_encoding')
                readme = base64.b64decode(document['content']).decode('utf-8')
                version = readme_version(readme)
                result.update(state='checked', available_version=version, commit=sha,
                              checked_at=result['attempted_at'])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
            _cache.update(time=time.monotonic(), result=result)
        result = dict(_cache['result'])
    result['installed_version'] = installed_version
    if result['state'] == 'checked':
        try:
            local, latest = version_key(installed_version), version_key(result['available_version'])
            result['state'] = 'available' if latest > local else 'current' if latest == local else 'ahead'
        except ValueError:
            result['state'] = 'unavailable'
    return result
