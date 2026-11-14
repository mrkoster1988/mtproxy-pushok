"""Публичный результат GitHub CI именно установленного коммита; без токенов."""
import json
import re
import time
import urllib.request
from threading import Lock

_cache = {}
_lock = Lock()


def lookup(commit):
    if not isinstance(commit,str) or not re.fullmatch('[0-9a-f]{40}',commit):
        return None
    with _lock:
        cached=_cache.get(commit)
        if cached and time.monotonic()-cached[0]<60:
            return cached[1]
    result=None
    try:
        request=urllib.request.Request(
            'https://api.github.com/repos/mrkoster1988/mtproxy-pushok/actions/runs?head_sha='+commit+'&event=push&per_page=20',
            headers={'Accept':'application/vnd.github+json','User-Agent':'Pushok-release-readiness'})
        with urllib.request.urlopen(request,timeout=3) as response:
            data=json.loads(response.read(1048576))
        runs=[r for r in data.get('workflow_runs',[]) if r.get('head_sha')==commit and r.get('name')=='API and PostgreSQL checks']
        if runs:
            run=max(runs,key=lambda r:r.get('run_number',0))
            result={'commit':commit,'verified':run.get('status')=='completed' and run.get('conclusion')=='success',
                    'status':run.get('status'),'conclusion':run.get('conclusion'),'checked_at':run.get('updated_at')}
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    with _lock:
        _cache.clear()
        _cache[commit]=(time.monotonic(),result)
    return result
