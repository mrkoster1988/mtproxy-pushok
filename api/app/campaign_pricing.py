"""Изменение условий одной неоплаченной заявки администратором, в одной транзакции."""
import hashlib
import json
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .growth import discounted


class PricingChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    tariff_id: UUID | None = None
    discount_percent: int | None = Field(default=None, ge=0, le=100, strict=True)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator('reason')
    @classmethod
    def trim_reason(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError('Укажите причину изменения')
        return value

    @model_validator(mode='after')
    def one_change(self):
        if (self.tariff_id is None) == (self.discount_percent is None):
            raise ValueError('Выберите смену тарифа или скидку')
        return self


def _current(c):
    stars = c['stars_price_snapshot'] if c['pricing_version'] == 1 else c['live_stars']
    return dict(tariff_id=str(c['tariff_id']), name=c['tariff_name_snapshot'],
        duration_seconds=c['duration_seconds_snapshot'], price_kopecks=c['price_kopecks_snapshot'],
        stars_price_xtr=stars, base_price_kopecks=c['base_price_kopecks_snapshot']
        if c['base_price_kopecks_snapshot'] is not None else c['price_kopecks_snapshot'],
        base_stars_price_xtr=c['base_stars_price_snapshot']
        if c['admin_discount_percent_snapshot'] is not None or c['base_stars_price_snapshot'] is not None else stars,
        admin_discount_percent=c['admin_discount_percent_snapshot'],
        discount_percent=c['discount_percent_snapshot'] or 0, offer_name=c['growth_offer_name'])


def _blocked(conn, c):
    if (c['archived_at'] or c['placement_status'] != 'not_scheduled'
            or c['moderation_status'] not in ('draft', 'pending', 'approved', 'changes_requested')
            or c['payment_status'] not in ('not_required', 'awaiting_payment', 'rejected')):
        return 'campaign_pricing_locked'
    if conn.execute('SELECT 1 FROM wallet_payments WHERE campaign_id=%s', (c['id'],)).fetchone():
        return 'campaign_pricing_locked'
    if conn.execute("SELECT 1 FROM payment_proofs WHERE campaign_id=%s AND reviewed_at IS NULL",
                    (c['id'],)).fetchone():
        return 'campaign_pricing_proof_pending'
    if conn.execute("""SELECT 1 FROM payment_attempts WHERE campaign_id=%s
        AND status IN ('created','pending','succeeded','refunded')
        AND NOT(provider='telegram_stars' AND status='pending' AND expires_at IS NOT NULL AND expires_at<=now()
                    AND precheckout_approved_at IS NULL) LIMIT 1""", (c['id'],)).fetchone():
        return 'campaign_pricing_invoice_open'
    # Полученное, но ещё не разобранное подтверждение не позволяет менять цену.
    if conn.execute("""SELECT 1 FROM stars_payment_receipts r JOIN payment_attempts a ON a.id=r.attempt_id
        WHERE a.campaign_id=%s AND r.handling_status<>'applied' LIMIT 1""", (c['id'],)).fetchone():
        return 'campaign_pricing_invoice_open'
    return None


def _read(conn, cid, *, lock=False):
    c = conn.execute('''SELECT c.*,t.stars_price_xtr AS live_stars,u.role AS owner_role
        FROM campaigns c JOIN tariffs t ON t.id=c.tariff_id
        JOIN users u ON u.id=c.advertiser_user_id WHERE c.id=%s'''
        + (' FOR UPDATE OF c' if lock else ''), (cid,)).fetchone()
    if not c:
        raise HTTPException(404, 'campaign_not_found')
    tariffs = conn.execute('''SELECT id,name,duration_seconds,price_kopecks,stars_price_xtr
        FROM tariffs WHERE is_active AND archived_at IS NULL AND (NOT admin_only OR %s)
        ORDER BY duration_seconds,price_kopecks,id''' + (' FOR SHARE' if lock else ''),
        (c['owner_role'] == 'admin',)).fetchall()
    tariffs = [{**t, 'id': str(t['id'])} for t in tariffs]
    live = next((t for t in tariffs if t['id'] == str(c['tariff_id'])), None)
    if live:
        c['live_stars'] = live['stars_price_xtr']
    current = _current(c)
    revision = hashlib.sha256(json.dumps(dict(current=current, tariffs=tariffs,
        updated_at=str(c['updated_at']), campaign_id=str(c['id']),
        moderation=c['moderation_status'], payment=c['payment_status'],
        placement=c['placement_status'], archived_at=str(c['archived_at'])),
        sort_keys=True).encode()).hexdigest()
    reason = _blocked(conn, c)
    return c, dict(current=current, tariffs=tariffs, revision=revision,
                  can_edit=reason is None, blocked_reason=reason)


def change_pricing(conn, cid, admin_id, payload):
    actor = conn.execute('SELECT role FROM users WHERE id=%s FOR SHARE', (admin_id,)).fetchone()
    if not actor or actor['role'] != 'admin':
        raise HTTPException(403, 'admin_role_required')
    c, state = _read(conn, cid, lock=True)
    if not state['can_edit']:
        raise HTTPException(409, state['blocked_reason'])
    if state['revision'] != payload.revision:
        raise HTTPException(409, 'campaign_pricing_changed')
    before = state['current']
    if payload.tariff_id is not None:
        tariff = next((t for t in state['tariffs'] if t['id'] == str(payload.tariff_id)), None)
        if not tariff:
            raise HTTPException(404, 'active_tariff_not_found')
        if payload.tariff_id == c['tariff_id']:
            raise HTTPException(409, 'campaign_pricing_same_tariff')
        base_rub, base_stars = tariff['price_kopecks'], tariff['stars_price_xtr']
        percent = 0
    else:
        if not any(t['id'] == str(c['tariff_id']) for t in state['tariffs']):
            raise HTTPException(409, 'campaign_pricing_tariff_unavailable')
        tariff = dict(id=str(c['tariff_id']), name=before['name'], duration_seconds=before['duration_seconds'])
        base_rub, base_stars = before['base_price_kopecks'], before['base_stars_price_xtr']
        percent = payload.discount_percent
        if c['admin_discount_percent_snapshot'] == percent:
            return state
    rub = discounted(base_rub, percent, rub=True)
    stars = discounted(base_stars, percent)
    if c['purchase_surface'] == 'telegram' and rub > 0 and not stars:
        raise HTTPException(409, 'campaign_pricing_stars_required')
    # Кампания блокируется тем же FOR UPDATE, что создание/подтверждение счетов и баланс.
    conn.execute("""UPDATE payment_attempts SET status='failed' WHERE campaign_id=%s
        AND provider='telegram_stars' AND status='pending' AND expires_at<=now()
        AND precheckout_approved_at IS NULL""", (cid,))
    if payload.tariff_id:
        conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s', (cid,))
    # Индивидуальные условия заменяют прежнюю акцию; её лимит не расходуется дважды.
    conn.execute('DELETE FROM growth_redemptions WHERE campaign_id=%s', (cid,))
    conn.execute('''UPDATE campaigns SET tariff_id=%s,tariff_name_snapshot=%s,
        duration_seconds_snapshot=%s,price_kopecks_snapshot=%s,currency_snapshot='RUB',
        pricing_version=1,base_price_kopecks_snapshot=%s,stars_price_snapshot=%s,
        base_stars_price_snapshot=%s,admin_discount_percent_snapshot=%s,
        growth_offer_id=NULL,growth_offer_name=%s,discount_percent_snapshot=%s,
        payment_status=CASE WHEN moderation_status='approved' THEN 'awaiting_payment' ELSE 'not_required' END,
        updated_at=now() WHERE id=%s''',
        (tariff['id'], tariff['name'], tariff['duration_seconds'], rub, base_rub, stars,
         base_stars, percent, 'Индивидуальная скидка' if percent else None, percent or None, cid))
    _, updated = _read(conn, cid)
    conn.execute('''INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json)
        VALUES(%s,%s,'campaign_pricing_changed',%s)''', (admin_id, cid,
        Jsonb(dict(before=before, after=updated['current'], reason=payload.reason,
                   change='tariff' if payload.tariff_id else 'discount'))))
    return updated


def router_for(get_dsn, require_admin):
    router = APIRouter(prefix='/api/v1/admin/campaigns', tags=['admin'])

    def db(action):
        try:
            with psycopg.connect(get_dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                return action(conn)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    @router.get('/{cid}/pricing')
    def read(cid: UUID, response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        def action(conn):
            _, state = _read(conn, cid)
            state['history'] = conn.execute('''SELECT created_at,details_json FROM audit_events
                WHERE campaign_id=%s AND event_type='campaign_pricing_changed'
                ORDER BY created_at DESC,id DESC LIMIT 10''', (cid,)).fetchall()
            return state
        return db(action)

    @router.patch('/{cid}/pricing')
    def update(cid: UUID, payload: PricingChange, response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        return db(lambda conn: change_pricing(conn, cid, admin['id'], payload))

    return router
