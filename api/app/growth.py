"""Серверный расчёт предложений; лимиты списываются атомарно вместе с заявкой."""
import base64
import hashlib
from functools import lru_cache
from io import BytesIO

import qrcode
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .bot_routes import _verify_bot_secret


@lru_cache(maxsize=128)
def invitation_qr(link: str) -> str:
    buffer = BytesIO()
    qrcode.make(link, box_size=6, border=4).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def telegram_invitation_link(code: str, *, miniapp: bool) -> str | None:
    bot = os.getenv('TELEGRAM_BOT_USERNAME', 'mtproxy_pushok_bot').lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', bot):
        parameter = '?startapp=r_' if miniapp else '?start=r_'
        return 'https://t.me/' + bot + parameter + code
    return None


def invitation_origin(request: Request, browser_origin: str | None) -> str | None:
    # Этот адрес возвращается только в личном приглашении текущего запроса.
    if browser_origin and len(browser_origin) <= 300:
        try:
            url = urlsplit(browser_origin)
            host = url.hostname or ''
            if (re.fullmatch(r'https://[a-zA-Z0-9.\-\[\]:]+', browser_origin)
                    and url.netloc.lower() == request.headers.get('host', '').lower()
                    and host and url.port != 0):
                return 'https://' + url.netloc.lower()
        except ValueError:
            pass
    from .gift_sharing import public_origin
    try:
        return public_origin()
    except HTTPException:
        return None


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class QuoteInput(Input):
    tariff_id: UUID
    code: str | None = Field(default=None, max_length=48)


class OfferInput(Input):
    kind: Literal['discount', 'promo', 'gift']
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=48)
    percent: int = Field(ge=1, le=100, strict=True)
    tariff_id: UUID | None = None
    recipient_user_id: UUID | None = None
    min_duration_seconds: int = Field(default=0, ge=0, le=31536000, strict=True)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    total_limit: int | None = Field(default=None, ge=1, le=1000000, strict=True)
    per_user_limit: int = Field(default=1, ge=1, le=1000000, strict=True)

    @field_validator('name')
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError('Укажите название')
        return value.strip()

    @model_validator(mode='after')
    def validate_offer(self):
        self.code = normalize_code(self.code)
        if self.kind == 'promo' and not re.fullmatch(r'[A-Z0-9_-]{3,48}', self.code or ''):
            raise ValueError('Промокод: 3–48 латинских букв, цифр, - или _')
        if self.kind == 'discount' and self.code:
            raise ValueError('Автоматическая скидка не требует кода')
        if self.kind == 'gift' and (not self.tariff_id or self.percent != 100):
            raise ValueError('Для подарка выберите тариф; скидка 100%')
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError('Укажите часовой пояс')
        if self.ends_at and self.ends_at <= (self.starts_at or datetime.now(timezone.utc)):
            raise ValueError('Дата окончания должна быть позже начала')
        return self


class OfferEdit(OfferInput):
    kind: Literal['discount', 'promo', 'gift', 'referral']


class ReferralSettings(Input):
    registration_bonus_kopecks: int | None = Field(default=None, ge=0, le=1000000, strict=True)
    referrals_enabled: bool
    reward_percent: int = Field(ge=1, le=100, strict=True)
    reward_days: int = Field(ge=1, le=365, strict=True)
    reward_mode: Literal['coupon', 'bonus'] = 'coupon'
    reward_bonus_kopecks: int = Field(default=10000, ge=1, le=1000000, strict=True)


class ReferralAssignment(Input):
    inviter_user_id: UUID


class ClaimInput(Input):
    code: str = Field(min_length=3, max_length=48)


class BotClaimInput(ClaimInput):
    telegram_user_id: int = Field(gt=0, lt=2**52)


class ToggleInput(Input):
    active: bool


def normalize_code(code):
    return code.strip().upper() if code and code.strip() else None


def discounted(amount, percent, *, rub=False):
    if amount is None:
        return None
    if percent == 100 or amount == 0:
        return 0
    result = (amount * (100 - percent) + 99) // 100
    # Платный СБП-счёт не может быть меньше одного рубля.
    return max(min(amount, 100), result) if rub else result


def quote(conn, user, tariff, code=None, *, lock=False):
    code = normalize_code(code)
    if code and tariff['price_kopecks'] == 0:
        raise HTTPException(409, 'growth_free_tariff')
    # Один и тот же порядок блокировок для параллельных покупок разных кодов.
    offers = conn.execute("""SELECT * FROM growth_offers
        WHERE (kind='discount' OR code=%s) AND active AND deleted_at IS NULL
          AND starts_at<=now() AND (ends_at IS NULL OR ends_at>now())
          AND (tariff_id IS NULL OR tariff_id=%s)
          AND min_duration_seconds<=%s
          AND (recipient_user_id IS NULL OR recipient_user_id=%s)
        ORDER BY id""" + (' FOR UPDATE' if lock else ''),
        (code, tariff['id'], tariff['duration_seconds'], user['id'])).fetchall()
    eligible = []
    if tariff['price_kopecks'] == 0:
        offers = []
    for offer in offers:
        counts = conn.execute("""SELECT count(*) AS total,count(*) FILTER(WHERE user_id=%s) AS own
            FROM growth_redemptions WHERE offer_id=%s""", (user['id'], offer['id'])).fetchone()
        if ((offer['total_limit'] is None or counts['total'] < offer['total_limit'])
                and counts['own'] < offer['per_user_limit']):
            eligible.append(offer)
    if code and not any(o['code'] == code for o in eligible):
        raise HTTPException(409, 'growth_code_unavailable')
    offer = max(eligible, key=lambda o: (o['percent'], o['code'] == code, str(o['id'])), default=None)
    percent = offer['percent'] if offer else 0
    result = dict(tariff_id=str(tariff['id']), duration_seconds=tariff['duration_seconds'],
        base_price_kopecks=tariff['price_kopecks'],
        price_kopecks=discounted(tariff['price_kopecks'], percent, rub=True),
        stars_price_xtr=discounted(tariff['stars_price_xtr'], percent),
        discount_percent=percent, offer_id=str(offer['id']) if offer else None,
        offer_name=offer['name'] if offer else None)
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def short_referral_code(conn, uid):
    """Стабильный короткий псевдоним; коллизия и параллельный запрос не меняют владельца."""
    row = conn.execute('SELECT code FROM growth_referral_links WHERE user_id=%s', (uid,)).fetchone()
    if row:
        return row['code']
    for _ in range(10):
        code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        row = conn.execute('''INSERT INTO growth_referral_links(code,user_id)
            SELECT %s,%s WHERE NOT EXISTS(SELECT 1 FROM growth_profiles WHERE code=%s)
            ON CONFLICT DO NOTHING RETURNING code''', (code,uid,code)).fetchone()
        if not row:
            row = conn.execute('SELECT code FROM growth_referral_links WHERE user_id=%s', (uid,)).fetchone()
        if row:
            return row['code']
    raise HTTPException(503, 'growth_link_unavailable')


def check_referral_cycle(conn, uid, inviter_id):
    cycle = conn.execute('''WITH RECURSIVE parents(id) AS (
        SELECT %s::uuid UNION SELECT r.inviter_user_id FROM growth_referrals r JOIN parents p ON r.referred_user_id=p.id)
        SELECT 1 FROM parents WHERE id=%s''', (inviter_id, uid)).fetchone()
    if cycle:
        raise HTTPException(409, 'growth_invitation_unavailable')


def claim_referral(conn, uid, code):
    conn.execute('SELECT pg_advisory_xact_lock(736102484)')
    conn.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (uid,))
    settings = conn.execute('SELECT * FROM growth_settings').fetchone()
    if not settings['referrals_enabled']:
        raise HTTPException(409, 'growth_referrals_disabled')
    inviter = conn.execute('''SELECT user_id FROM growth_profiles WHERE code=%s
        UNION SELECT user_id FROM growth_referral_links WHERE code=%s''', (normalize_code(code),normalize_code(code))).fetchone()
    if not inviter or inviter['user_id'] == uid:
        raise HTTPException(409, 'growth_invitation_unavailable')
    old = conn.execute('SELECT inviter_user_id FROM growth_referrals WHERE referred_user_id=%s', (uid,)).fetchone()
    if old:
        if old['inviter_user_id'] == inviter['user_id']:
            return {'saved': True}
        raise HTTPException(409, 'growth_invitation_already_bound')
    if conn.execute('SELECT 1 FROM campaigns WHERE advertiser_user_id=%s LIMIT 1', (uid,)).fetchone():
        raise HTTPException(409, 'growth_invitation_before_order')
    check_referral_cycle(conn, uid, inviter['user_id'])
    conn.execute('''INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,reward_mode,reward_bonus_kopecks)
        VALUES(%s,%s,%s,%s,%s,%s)''', (uid, inviter['user_id'], settings['reward_percent'], settings['reward_days'],
                                   settings['reward_mode'], settings['reward_bonus_kopecks']))
    conn.execute("INSERT INTO audit_events(actor_user_id,event_type) VALUES(%s,'referral_bound')", (uid,))
    return {'saved': True}


def router_for(get_dsn, get_user, require_admin):
    router = APIRouter(prefix='/api/v1', tags=['growth'])

    def db(action):
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                return action(conn)
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(409, 'growth_code_exists') from exc
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    def no_cache(response):
        response.headers['Cache-Control'] = 'private, no-store'

    def admin_lock(conn, admin):
        actor = conn.execute('SELECT role FROM users WHERE id=%s FOR SHARE', (admin['id'],)).fetchone()
        if not actor or actor['role'] != 'admin':
            raise HTTPException(403, 'admin_role_required')

    @router.post('/growth/quote')
    def price(payload: QuoteInput, response: Response, user=Depends(get_user)):
        no_cache(response)
        def action(conn):
            tariff = conn.execute('SELECT * FROM tariffs WHERE id=%s AND is_active AND archived_at IS NULL', (payload.tariff_id,)).fetchone()
            if not tariff or (tariff['admin_only'] and user['role'] != 'admin'):
                raise HTTPException(404, 'active_tariff_not_found')
            return quote(conn, user, tariff, payload.code)
        return db(action)

    @router.get('/growth/shared/{offer_id}')
    def shared(offer_id: UUID, response: Response, user=Depends(get_user)):
        no_cache(response)
        def action(conn):
            # Публичная ссылка не отменяет получателя, сроки или лимит купона.
            offer=conn.execute("""SELECT * FROM growth_offers WHERE id=%s AND active AND deleted_at IS NULL
                AND starts_at<=now() AND (ends_at IS NULL OR ends_at>now())
                AND (recipient_user_id IS NULL OR recipient_user_id=%s)""",(offer_id,user['id'])).fetchone()
            if not offer:raise HTTPException(409,'growth_code_unavailable')
            counts=conn.execute('SELECT count(*) AS total,count(*) FILTER(WHERE user_id=%s) AS own FROM growth_redemptions WHERE offer_id=%s',(user['id'],offer_id)).fetchone()
            if (offer['total_limit'] is not None and counts['total']>=offer['total_limit']) or counts['own']>=offer['per_user_limit']:
                raise HTTPException(409,'growth_code_unavailable')
            tariffs=conn.execute("""SELECT * FROM tariffs WHERE is_active AND archived_at IS NULL AND price_kopecks>0
                AND (NOT admin_only OR %s='admin') AND (%s::uuid IS NULL OR id=%s)
                AND duration_seconds>=%s ORDER BY price_kopecks,id""",(user['role'],offer['tariff_id'],offer['tariff_id'],offer['min_duration_seconds'])).fetchall()
            for tariff in tariffs:
                if user.get('login_surface')=='telegram' and offer['percent']<100 and not tariff['stars_price_xtr']:
                    continue
                try:pricing=quote(conn,user,tariff,offer['code'])
                except HTTPException:continue
                return dict(id=str(offer['id']),name=offer['name'],code=offer['code'],tariff_id=str(tariff['id']),quote=pricing)
            raise HTTPException(409,'growth_code_unavailable')
        return db(action)

    public = APIRouter()

    def invite_redirect(code: str, request: Request, *, proxy: bool):
        headers = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
                   'X-Robots-Tag': 'noindex, nofollow'}
        code = code.upper()
        if not re.fullmatch(r'[A-HJ-NP-Z2-9]{8}', code) or not db(lambda conn: conn.execute(
            """SELECT 1 FROM growth_referral_links r CROSS JOIN growth_settings s
               WHERE r.code=%s AND s.referrals_enabled""", (code,)).fetchone()):
            return PlainTextResponse('Это приглашение недоступно.', status_code=404, headers=headers)
        target = '/'
        if proxy:
            from .proxy_link import current_proxy_url
            target = current_proxy_url()
            if not target:
                return PlainTextResponse('Прокси пока недоступен. Откройте эту же ссылку позднее.',
                                         status_code=503, headers={**headers, 'Retry-After': '60'})
        elif os.getenv('PUSHOK_MINIAPP_ENABLED', '0') == '1':
            app_link = telegram_invitation_link(code, miniapp=True)
            if app_link:
                return RedirectResponse(app_link, status_code=302, headers=headers)
        result = RedirectResponse(target, status_code=302, headers=headers)
        # Переход относится к браузеру. Личность друга подтверждается при входе в кабинет.
        if (request.headers.get('sec-fetch-mode', 'navigate') == 'navigate'
                and not re.fullmatch(r'[A-HJ-NP-Z2-9]{8}', request.cookies.get('__Host-pushok_invite', ''))):
            result.set_cookie('__Host-pushok_invite', code, max_age=30*86400, secure=True,
                              samesite='lax', path='/')
        return result

    @public.get('/p/{code}', include_in_schema=False)
    def open_proxy_invitation(code: str, request: Request):
        return invite_redirect(code, request, proxy=True)

    @public.get('/invite/{code}', include_in_schema=False)
    def open_cabinet_invitation(code: str, request: Request):
        return invite_redirect(code, request, proxy=False)

    @router.get('/growth/me')
    def mine(request: Request, response: Response, invite_origin: str | None = None, user=Depends(get_user)):
        no_cache(response)
        def action(conn):
            conn.execute('INSERT INTO growth_profiles(user_id) VALUES(%s) ON CONFLICT DO NOTHING', (user['id'],))
            code = short_referral_code(conn, user['id'])
            settings = conn.execute('SELECT * FROM growth_settings').fetchone()
            coupons = conn.execute('''SELECT o.id,o.name,o.code,o.percent,o.ends_at,o.active,
                EXISTS(SELECT 1 FROM growth_redemptions d WHERE d.offer_id=o.id) AS used
                FROM growth_offers o WHERE recipient_user_id=%s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100''', (user['id'],)).fetchall()
            count = conn.execute('SELECT count(*) AS n FROM growth_referrals WHERE inviter_user_id=%s', (user['id'],)).fetchone()['n']
            bonus = conn.execute('''SELECT coalesce(sum(amount),0)::bigint AS credited,
                coalesce(sum(amount) FILTER(WHERE reversal_id IS NOT NULL),0)::bigint AS reversed
                FROM growth_wallet_rewards WHERE inviter_user_id=%s''', (user['id'],)).fetchone()
            # Пригласившему не выдаём телефоны, Telegram ID или заявки друга.
            invitations = conn.execute('''SELECT r.created_at,r.reward_mode,r.reward_percent,r.reward_days,r.reward_bonus_kopecks,
                CASE WHEN w.inviter_user_id<>r.inviter_user_id OR o.recipient_user_id<>r.inviter_user_id THEN 'previous_inviter'
                     WHEN w.reversal_id IS NOT NULL OR (o.id IS NOT NULL AND NOT o.active) THEN 'reversed'
                     WHEN w.entry_id IS NOT NULL OR o.id IS NOT NULL THEN 'rewarded' ELSE 'waiting' END AS status
                FROM growth_referrals r LEFT JOIN growth_wallet_rewards w ON w.referred_user_id=r.referred_user_id
                LEFT JOIN growth_offers o ON o.reward_for_user_id=r.referred_user_id
                WHERE r.inviter_user_id=%s ORDER BY r.created_at DESC,r.referred_user_id LIMIT 100''', (user['id'],)).fetchall()
            bound = conn.execute('SELECT 1 FROM growth_referrals WHERE referred_user_id=%s', (user['id'],)).fetchone()
            miniapp = os.getenv('PUSHOK_MINIAPP_ENABLED', '0') == '1'
            link = telegram_invitation_link(code, miniapp=True) if miniapp else None
            if not link:
                origin = invitation_origin(request, invite_origin)
                link = origin + '/invite/' + code if origin else telegram_invitation_link(code, miniapp=False)
            return {**settings, 'code':code, 'link':link, 'proxy_invite':False,
                    'invite_qr':invitation_qr(link) if link and settings['referrals_enabled'] else None,
                    'invited':count, 'bound':bool(bound),
                    'coupons':coupons, 'bonus_rewards':bonus, 'invitations':invitations}
        return db(action)

    @router.post('/growth/referral')
    def claim(payload: ClaimInput, response: Response, user=Depends(get_user)):
        no_cache(response)
        return db(lambda conn: claim_referral(conn, user['id'], payload.code))

    @router.post('/internal/bot/referral', include_in_schema=False)
    def bot_claim(payload: BotClaimInput, response: Response, x_pushok_bot_secret: str | None = Header(default=None)):
        _verify_bot_secret(x_pushok_bot_secret)
        no_cache(response)
        def action(conn):
            conn.execute('INSERT INTO users(telegram_user_id) VALUES(%s) ON CONFLICT DO NOTHING', (payload.telegram_user_id,))
            user = conn.execute('SELECT id,blocked_at FROM users WHERE telegram_user_id=%s FOR SHARE', (payload.telegram_user_id,)).fetchone()
            if user['blocked_at'] is not None:raise HTTPException(403,'user_blocked')
            uid=user['id']
            return claim_referral(conn, uid, payload.code)
        return db(action)

    @router.get('/admin/growth')
    def admin_data(response: Response, deleted: bool = False, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            return {'settings':conn.execute('SELECT * FROM growth_settings').fetchone(),
                'offers':conn.execute('''SELECT o.*,t.name AS tariff_name,u.username AS recipient_username,u.telegram_user_id AS recipient_telegram_id,
                    (SELECT count(*) FROM growth_redemptions d WHERE d.offer_id=o.id) AS uses
                    FROM growth_offers o LEFT JOIN tariffs t ON t.id=o.tariff_id LEFT JOIN users u ON u.id=o.recipient_user_id
                    WHERE (o.deleted_at IS NOT NULL)=%s ORDER BY o.created_at DESC LIMIT 200''',(deleted,)).fetchall()}
        return db(action)

    @router.put('/admin/growth/referrals/{uid}')
    def assign_referral(uid: UUID, payload: ReferralAssignment, admin=Depends(require_admin)):
        def action(conn):
            admin_lock(conn, admin)
            conn.execute('SELECT pg_advisory_xact_lock(736102484)')
            inviter = payload.inviter_user_id
            accounts = conn.execute("SELECT id FROM users WHERE id IN (%s,%s) AND telegram_user_id>0",
                                    (uid, inviter)).fetchall()
            if uid == inviter:
                raise HTTPException(409, 'growth_invitation_unavailable')
            if len(accounts) != 2:
                raise HTTPException(404, 'user_profile_not_found')
            check_referral_cycle(conn, uid, inviter)
            previous = conn.execute("SELECT inviter_user_id FROM growth_referrals WHERE referred_user_id=%s FOR UPDATE",
                                    (uid,)).fetchone()
            if previous and previous['inviter_user_id'] == inviter:
                return {'saved': True}
            conn.execute("""INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_mode,
                    reward_percent,reward_days,reward_bonus_kopecks)
                SELECT %s,%s,reward_mode,reward_percent,reward_days,reward_bonus_kopecks FROM growth_settings
                ON CONFLICT (referred_user_id) DO UPDATE SET inviter_user_id=EXCLUDED.inviter_user_id""", (uid, inviter))
            conn.execute("""INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'referral_bound',
                jsonb_build_object('referred',%s::uuid,'previous_inviter',%s::uuid,'inviter',%s::uuid))""",
                (admin['id'], uid, previous['inviter_user_id'] if previous else None, inviter))
            return {'saved': True}
        return db(action)

    @router.delete('/admin/growth/referrals/{uid}/from/{inviter_id}')
    def remove_referral(uid: UUID, inviter_id: UUID, admin=Depends(require_admin)):
        def action(conn):
            admin_lock(conn, admin)
            conn.execute('SELECT pg_advisory_xact_lock(736102484)')
            removed = conn.execute("""DELETE FROM growth_referrals WHERE referred_user_id=%s AND inviter_user_id=%s
                RETURNING referred_user_id""", (uid, inviter_id)).fetchone()
            if removed:
                conn.execute("""INSERT INTO audit_events(actor_user_id,event_type,details_json)
                    VALUES(%s,'referral_bound',jsonb_build_object('referred',%s::uuid,'previous_inviter',%s::uuid,'inviter',NULL))""",
                    (admin['id'], uid, inviter_id))
            return {'removed': bool(removed)}
        return db(action)

    @router.put('/admin/growth/settings')
    def settings(payload: ReferralSettings, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            conn.execute('''UPDATE growth_settings SET referrals_enabled=%s,reward_percent=%s,reward_days=%s,
                reward_mode=%s,reward_bonus_kopecks=%s,
                registration_bonus_kopecks=coalesce(%s,registration_bonus_kopecks)''',
                         (payload.referrals_enabled, payload.reward_percent, payload.reward_days,
                          payload.reward_mode, payload.reward_bonus_kopecks, payload.registration_bonus_kopecks))
            if payload.registration_bonus_kopecks:
                conn.execute('''SELECT pushok_registration_bonus(u.id,%s) FROM users u
                    WHERE u.telegram_user_id>0 AND NOT EXISTS (SELECT 1 FROM wallet_entries w
                        WHERE w.operation_key='registration:'||u.id) ORDER BY u.id''', (payload.registration_bonus_kopecks,))
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_settings_changed',%s::jsonb)", (admin['id'], payload.model_dump_json()))
            return {'saved':True}
        return db(action)

    @router.post('/admin/growth/offers', status_code=201)
    def create(payload: OfferInput, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            if payload.recipient_user_id and not conn.execute('SELECT 1 FROM users WHERE id=%s',(payload.recipient_user_id,)).fetchone():
                raise HTTPException(404,'user_profile_not_found')
            if payload.tariff_id:
                tariff = conn.execute('SELECT price_kopecks FROM tariffs WHERE id=%s AND NOT admin_only AND archived_at IS NULL', (payload.tariff_id,)).fetchone()
                if not tariff:
                    raise HTTPException(404, 'active_tariff_not_found')
                if payload.kind == 'gift' and tariff['price_kopecks'] == 0:
                    raise HTTPException(409, 'growth_free_tariff')
            import secrets
            code = secrets.token_hex(12).upper() if payload.kind == 'gift' else payload.code
            offer = conn.execute('''INSERT INTO growth_offers(kind,name,code,percent,tariff_id,min_duration_seconds,
                starts_at,ends_at,total_limit,per_user_limit,created_by,recipient_user_id)
                VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),%s,%s,%s,%s,%s) RETURNING *''',
                (payload.kind,payload.name,code,payload.percent,payload.tariff_id,payload.min_duration_seconds,
                 payload.starts_at,payload.ends_at,1 if payload.kind=='gift' else payload.total_limit,
                 payload.per_user_limit,admin['id'],payload.recipient_user_id)).fetchone()
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_offer_created',jsonb_build_object('offer_id',%s::uuid))", (admin['id'],offer['id']))
            return offer
        return db(action)

    @router.patch('/admin/growth/offers/{offer_id}')
    def toggle(offer_id: UUID, payload: ToggleInput, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            row = conn.execute("UPDATE growth_offers SET active=%s WHERE id=%s AND deleted_at IS NULL AND (kind<>'referral' OR NOT %s) RETURNING id", (payload.active,offer_id,payload.active)).fetchone()
            if not row:
                raise HTTPException(404, 'growth_offer_not_found')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_offer_toggled',jsonb_build_object('offer_id',%s::uuid,'active',%s::boolean))", (admin['id'],offer_id,payload.active))
            return {'saved':True}
        return db(action)
    @router.put('/admin/growth/offers/{offer_id}')
    def edit(offer_id: UUID, payload: OfferEdit, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            old=conn.execute('SELECT * FROM growth_offers WHERE id=%s AND deleted_at IS NULL FOR UPDATE',(offer_id,)).fetchone()
            if not old:raise HTTPException(404,'growth_offer_not_found')
            data=payload.model_dump()
            data['starts_at']=data['starts_at'] or old['starts_at']
            if old['kind'] in ('gift','referral'):data['code']=old['code']
            if data['kind']!=old['kind']:raise HTTPException(409,'growth_kind_locked')
            used=conn.execute('SELECT count(*) AS total,coalesce(max(n),0) AS per_user FROM (SELECT count(*) AS n FROM growth_redemptions WHERE offer_id=%s GROUP BY user_id) q',(offer_id,)).fetchone()
            total=conn.execute('SELECT count(*) AS n FROM growth_redemptions WHERE offer_id=%s',(offer_id,)).fetchone()['n']
            if total and any(data[key]!=old[key] for key in ('code','recipient_user_id')):
                raise HTTPException(409,'growth_used_identity_locked')
            if (data['total_limit'] is not None and data['total_limit']<total) or data['per_user_limit']<used['per_user']:
                raise HTTPException(409,'growth_limit_used')
            if old['kind']=='referral' and any(data[key]!=old[key] for key in data if key not in ('name','ends_at')):
                raise HTTPException(409,'growth_referral_terms_locked')
            if data['recipient_user_id'] and not conn.execute('SELECT 1 FROM users WHERE id=%s',(data['recipient_user_id'],)).fetchone():
                raise HTTPException(404,'user_profile_not_found')
            if data['tariff_id']!=old['tariff_id'] and data['tariff_id'] and not conn.execute('SELECT 1 FROM tariffs WHERE id=%s AND is_active AND NOT admin_only AND archived_at IS NULL',(data['tariff_id'],)).fetchone():
                raise HTTPException(404,'active_tariff_not_found')
            row=conn.execute("""UPDATE growth_offers SET name=%s,code=%s,percent=%s,tariff_id=%s,
                min_duration_seconds=%s,starts_at=%s,ends_at=%s,total_limit=%s,per_user_limit=%s,
                recipient_user_id=%s WHERE id=%s RETURNING *""",
                tuple(data[key] for key in ('name','code','percent','tariff_id','min_duration_seconds','starts_at','ends_at','total_limit','per_user_limit','recipient_user_id'))+(offer_id,)).fetchone()
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_offer_edited',jsonb_build_object('offer_id',%s::uuid,'changes',%s::jsonb))",(admin['id'],offer_id,payload.model_dump_json()))
            return row
        return db(action)

    @router.delete('/admin/growth/offers/{offer_id}')
    def delete(offer_id: UUID, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            row=conn.execute('UPDATE growth_offers SET active=false,deleted_at=coalesce(deleted_at,now()) WHERE id=%s RETURNING id',(offer_id,)).fetchone()
            if not row:raise HTTPException(404,'growth_offer_not_found')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_offer_deleted',jsonb_build_object('offer_id',%s::uuid))",(admin['id'],offer_id))
            return {'saved':True}
        return db(action)

    @router.post('/admin/growth/offers/{offer_id}/restore')
    def restore(offer_id: UUID, response: Response, admin=Depends(require_admin)):
        no_cache(response)
        def action(conn):
            admin_lock(conn, admin)
            row=conn.execute('UPDATE growth_offers SET active=false,deleted_at=NULL WHERE id=%s AND deleted_at IS NOT NULL RETURNING id',(offer_id,)).fetchone()
            if not row:raise HTTPException(404,'growth_offer_not_found')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'growth_offer_restored',jsonb_build_object('offer_id',%s::uuid))",(admin['id'],offer_id))
            return {'saved':True}
        return db(action)
    public.include_router(router)
    return public
