"""Serve the verified public Android package as a Telegram Mini App attachment."""
from functools import lru_cache
import hashlib
import re
from threading import Lock
import time

import requests
from fastapi import APIRouter, HTTPException, Request, Response

router=APIRouter()
RELEASES='https://github.com/mrkoster1988/Pushok-Flow/releases'
CERTIFICATE='8b4114881116a920c3351b503052ac27078fdbbfcae1ace27f1af8245ebb7047'
lock=Lock()
latest=None
refresh_at=0.0


def metadata():
    global latest, refresh_at
    if time.monotonic()>=refresh_at:
        with requests.get(RELEASES+'/latest/download/update.json',timeout=(3,8)) as result:
            result.raise_for_status()
            data=result.json()
        version=data['versionName']
        if not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',version):
            raise ValueError('version')
        name=f'Pushok-Flow-v{version}.apk'
        url=f'{RELEASES}/download/v{version}/{name}'
        if (data['apkUrl']!=url or data['packageName']!='shop.pushok.flow' or data['certificateSha256']!=CERTIFICATE
                or not re.fullmatch(r'[a-f0-9]{64}',data['sha256']) or not 0<data['sizeBytes']<=80*1024*1024):
            raise ValueError('manifest')
        latest=(name,url,data['sha256'],data['sizeBytes'])
        refresh_at=time.monotonic()+300
    return latest


@lru_cache(maxsize=1)
def package(url,digest,size):
    with requests.get(url,stream=True,timeout=(5,30)) as source:
        source.raise_for_status()
        chunks=[]; received=0
        for chunk in source.iter_content(256*1024):
            received+=len(chunk)
            if received>size: raise ValueError('size')
            chunks.append(chunk)
    content=b''.join(chunks)
    if len(content)!=size or hashlib.sha256(content).hexdigest()!=digest:
        raise ValueError('invalid package')
    return content


@router.api_route('/api/v1/vpn/flow.apk',methods=['GET','HEAD'],include_in_schema=False)
def download(request:Request):
    try:
        with lock:
            name,url,digest,size=metadata()
            content=b'' if request.method=='HEAD' else package(url,digest,size)
        return Response(content,media_type='application/vnd.android.package-archive',headers={
            'Content-Disposition':f'attachment; filename="{name}"','Content-Length':str(size),
            'Access-Control-Allow-Origin':'https://web.telegram.org','Cache-Control':'public, max-age=300',
            'ETag':'"'+digest+'"'})
    except (ValueError,TypeError,KeyError,requests.RequestException):
        raise HTTPException(503,'vpn_download_unavailable') from None
