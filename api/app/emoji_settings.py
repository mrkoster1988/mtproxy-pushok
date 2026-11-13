"""Чтение настоящих ID PushokIcons; привязку действий выбирает администратор."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from .stars_checkout import credentials
from .telegram_environment import bot_api_url
from . import control
from fastapi import HTTPException

ACTIONS = ('miniapp','advertise','campaigns','tariffs','channels','help')
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch():
    token, _ = credentials()
    req = Request(bot_api_url(token, 'getStickerSet'), data=json.dumps({'name':'PushokIcons'}).encode(),
                  headers={'Content-Type':'application/json'})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
            data = json.loads(response.read(1024*1024))
        pack = data['result']
        if data.get('ok') is not True or pack['name'].lower() != 'pushokicons' or pack['sticker_type'] != 'custom_emoji':
            raise ValueError()
        items = [{'id': x['custom_emoji_id'], 'emoji': x.get('emoji','')} for x in pack['stickers']]
        if not items or len(items)>100 or any(not x['id'].isdigit() for x in items):
            raise ValueError()
    except Exception:
        raise HTTPException(502, 'pushok_icons_catalog_unavailable') from None
    control.write('emoji-catalog.json', items)
    return items

def save(mapping):
    ids = {x['id'] for x in control.read('emoji-catalog.json', [])}
    if set(mapping)-set(ACTIONS) or any(not isinstance(v,str) or v not in ids for v in mapping.values()):
        raise HTTPException(422, 'unknown_pushok_icon')
    control.write('emoji-map.json', mapping)
