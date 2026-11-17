"""Поиск публичного канала по точному username и отчёт по доступному каналу."""
import time
from collections import OrderedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from . import channel_logos as logos
from .public_channels import reference
from . import public_channels

_requests = OrderedDict()


def lookup(value):
    return public_channels.lookup(value)


def router_for(dsn, current_user):
    router = APIRouter()

    def throttle(uid):
        now = time.monotonic()
        with logos._lock:
            recent = [t for t in _requests.get(uid, []) if now - t < 60]
            if len(recent) >= 30:
                raise HTTPException(429, 'channel_lookup_rate_limit')
            _requests[uid] = recent + [now]
            _requests.move_to_end(uid)
            while len(_requests) > 512:
                _requests.popitem(last=False)

    @router.get('/api/v1/channel-preview')
    def preview(response: Response, query: str = Query(min_length=4, max_length=255), user=Depends(current_user)):
        throttle(user['id'])
        response.headers['Cache-Control'] = 'private, no-store'
        return lookup(query)

    @router.get('/api/v1/channel-preview/logo')
    def preview_logo(query: str = Query(min_length=4, max_length=255), user=Depends(current_user)):
        username = reference(query)
        throttle(user['id'])
        data = logos.logo('https://t.me/' + username)
        return Response(data, status_code=200 if data else 204, media_type='image/jpeg' if data else None,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/api/v1/channels/{channel_id}/report')
    def report(channel_id: UUID, user=Depends(current_user)):
        try:
            with psycopg.connect(dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                channel = conn.execute('SELECT id,owner_user_id,title,telegram_url FROM sponsor_channels WHERE id=%s', (channel_id,)).fetchone()
                if not channel or (user['role'] not in ('admin', 'operator') and channel['owner_user_id'] != user['id']):
                    raise HTTPException(404, 'channel_not_found')
                totals = conn.execute("SELECT count(*) AS total,count(*) FILTER(WHERE placement_status='completed') AS completed,count(*) FILTER(WHERE placement_status='active') AS active FROM campaigns WHERE channel_id=%s", (channel_id,)).fetchone()
                campaigns = conn.execute('SELECT id,tariff_name_snapshot,placement_status,payment_status,archived_at,scheduled_start_at,scheduled_end_at FROM campaigns WHERE channel_id=%s ORDER BY created_at DESC,id DESC LIMIT 30', (channel_id,)).fetchall()
                return dict(channel=dict(id=channel['id'], title=channel['title'], telegram_url=channel['telegram_url']), totals=totals, campaigns=campaigns)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    return router
