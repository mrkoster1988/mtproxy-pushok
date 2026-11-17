"""Профиль доступен владельцу и сотрудникам; фото не раскрывает токен Bot API."""
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .channel_logos import user_avatar
from .verified_profiles import private_profile
from .telegram_environment import environment_name

PAGE_SIZE = 10
ARCHIVED = "(c.archived_at IS NOT NULL OR c.placement_status IN ('completed','cancelled'))"
PRIVATE_HEADERS = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}


def _user(conn, uid, viewer):
    if viewer['id'] != uid and viewer['role'] not in ('admin', 'operator'):
        raise HTTPException(404, 'user_profile_not_found')
    row = conn.execute('''SELECT id,telegram_user_id,first_name,last_name,username,role,created_at,blocked_at,
        EXISTS(SELECT 1 FROM verified_profiles v WHERE v.user_id=users.id AND v.phone_verified_at IS NOT NULL AND v.telegram_environment=%s) AS phone_verified
        FROM users WHERE id=%s''', (environment_name(), uid)).fetchone()
    if not row:
        raise HTTPException(404, 'user_profile_not_found')
    return row


def router_for(dsn, current_user):
    router = APIRouter(prefix='/api/v1/users', tags=['profiles'])

    def db(action):
        try:
            with psycopg.connect(dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                return action(conn)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    @router.get('/{uid}/profile')
    def profile(uid: UUID, response: Response, archived: bool = False,
                campaign_page: int = Query(default=0, ge=0, le=100000),
                channel_page: int = Query(default=0, ge=0, le=100000), viewer=Depends(current_user)):
        response.headers.update(PRIVATE_HEADERS)
        def read(conn):
            # Все счётчики и страницы принадлежат одному снимку БД.
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            user = _user(conn, uid, viewer)
            counts = conn.execute(f'''SELECT count(*) AS total,
                count(*) FILTER (WHERE NOT {ARCHIVED}) AS current,
                count(*) FILTER (WHERE {ARCHIVED}) AS archived,
                count(*) FILTER (WHERE c.placement_status='active' AND NOT {ARCHIVED}) AS active
                FROM campaigns c WHERE c.advertiser_user_id=%s''', (uid,)).fetchone()
            counts['channels'] = conn.execute('''SELECT count(*) AS n FROM sponsor_channels
                WHERE owner_user_id=%s AND archived_at IS NULL''', (uid,)).fetchone()['n']
            counts['invited'] = conn.execute('SELECT count(*) AS n FROM growth_referrals WHERE inviter_user_id=%s', (uid,)).fetchone()['n']
            channels = conn.execute('''SELECT id,title,telegram_url FROM sponsor_channels
                WHERE owner_user_id=%s AND archived_at IS NULL ORDER BY created_at DESC,id DESC
                LIMIT %s OFFSET %s''', (uid, PAGE_SIZE, channel_page * PAGE_SIZE)).fetchall()
            campaigns = conn.execute(f'''SELECT c.id,c.channel_id,ch.title AS channel_title,
                ch.telegram_url AS channel_url,c.tariff_name_snapshot,c.duration_seconds_snapshot,
                c.price_kopecks_snapshot,c.currency_snapshot,c.moderation_status,c.payment_status,
                c.placement_status,c.requested_start_at,c.scheduled_start_at,c.scheduled_end_at,c.archived_at,
                CASE WHEN c.pricing_version=1 THEN c.stars_price_snapshot ELSE t.stars_price_xtr END AS stars_price_snapshot
                FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id JOIN tariffs t ON t.id=c.tariff_id
                WHERE c.advertiser_user_id=%s AND {ARCHIVED}=%s ORDER BY c.created_at DESC,c.id DESC
                LIMIT %s OFFSET %s''', (uid, archived, PAGE_SIZE, campaign_page * PAGE_SIZE)).fetchall()
            from .hall_of_fame import medal_history
            private = {'medals': medal_history(conn, uid)}
            private['frame_rank'] = conn.execute('''SELECT min(rank) rank FROM hall_results WHERE user_id=%s AND rank<=10
                AND period_id=(SELECT id FROM hall_periods WHERE kind='month' AND closed_at IS NOT NULL ORDER BY starts_at DESC LIMIT 1)''',(uid,)).fetchone()['rank']
            if viewer['id'] == uid or viewer['role'] == 'admin':
                private['verification'] = private_profile(conn, uid)
            if viewer['role'] == 'admin':
                balances = {currency: 0 for currency in ('RUB', 'BONUS', 'XTR')}
                balances.update({row['currency']: row['balance'] for row in conn.execute(
                    'SELECT currency,balance FROM wallet_accounts WHERE user_id=%s', (uid,)).fetchall()})
                private['wallet'] = balances
            return dict(user=user, counts=counts, channels=channels, campaigns=campaigns, **private,
                channel_page=channel_page, campaign_page=campaign_page, archived=archived,
                more_channels=(channel_page+1)*PAGE_SIZE < counts['channels'],
                more_campaigns=(campaign_page+1)*PAGE_SIZE < counts['archived' if archived else 'current'])
        return db(read)

    @router.get('/{uid}/avatar')
    def avatar(uid: UUID, viewer=Depends(current_user)):
        user = db(lambda conn: _user(conn, uid, viewer))
        data = user_avatar(user['telegram_user_id'])
        return Response(data, status_code=200 if data else 204, headers=PRIVATE_HEADERS,
                        media_type='image/jpeg' if data else None)

    return router
