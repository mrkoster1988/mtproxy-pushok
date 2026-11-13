"""Чтение настоящих ID PushokIcons; привязку действий выбирает администратор."""
import json
import base64
import io
import re
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from .stars_checkout import credentials
from .telegram_environment import bot_api_url, is_test_environment
from . import control
from fastapi import HTTPException

ACTIONS = ('miniapp','advertise','campaigns','tariffs','channels','help')
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def read_response(request, limit):
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=6) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('response too large')
    return raw


def preview(token, sticker):
    try:
        file_id = (sticker.get('thumbnail') or {}).get('file_id')
        if not file_id and (sticker.get('is_animated') or sticker.get('is_video')):
            return None
        file_id = file_id or sticker['file_id']
        request = Request(bot_api_url(token, 'getFile'), data=json.dumps({'file_id':file_id}).encode(),
                          headers={'Content-Type':'application/json'})
        result = json.loads(read_response(request, 16384))
        if result.get('ok') is not True:
            return None
        path = result['result']['file_path']
        if not re.fullmatch(r'[A-Za-z0-9_/-]+\.(?:webp|jpg|jpeg|png)', path) or any(x in ('','..','.') for x in path.split('/')):
            return None
        url = 'https://api.telegram.org/file/bot' + token + ('/test/' if is_test_environment() else '/') + path
        raw = read_response(Request(url), 512*1024)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('WEBP','JPEG','PNG') or not (0 < image.width <= 1024 and 0 < image.height <= 1024):
                return None
            image.load()
            image = image.convert('RGBA')
            image.info.clear()
            image.thumbnail((160,160))
            output = io.BytesIO()
            image.save(output, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    except Exception:
        # Не отдаём токен, URL скачивания и необработанные файлы в браузер.
        return None

def fetch():
    token, _ = credentials()
    req = Request(bot_api_url(token, 'getStickerSet'), data=json.dumps({'name':'PushokIcons'}).encode(),
                  headers={'Content-Type':'application/json'})
    try:
        data = json.loads(read_response(req, 1024*1024))
        pack = data['result']
        if data.get('ok') is not True or pack['name'].lower() != 'pushokicons' or pack['sticker_type'] != 'custom_emoji':
            raise ValueError()
        items = [{'id': x['custom_emoji_id'], 'emoji': x.get('emoji','')} for x in pack['stickers']]
        if not items or len(items)>24 or any(not isinstance(x['id'],str) or not re.fullmatch(r'[1-9][0-9]{0,29}', x['id']) for x in items) or len({x['id'] for x in items}) != len(items):
            raise ValueError()
    except Exception:
        raise HTTPException(502, 'pushok_icons_catalog_unavailable') from None
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = list(pool.map(lambda sticker: preview(token, sticker), pack['stickers']))
    for item, image in zip(items, images):
        item['image'] = image
    control.write('emoji-catalog.json', items)
    return items

def save(mapping):
    ids = {x['id'] for x in control.read('emoji-catalog.json', [])}
    if set(mapping)-set(ACTIONS) or any(not isinstance(v,str) or v not in ids for v in mapping.values()):
        raise HTTPException(422, 'unknown_pushok_icon')
    control.write('emoji-map.json', mapping)
