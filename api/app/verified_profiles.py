"""Собственный контакт Telegram, приватные сведения и ежегодный подарок.

Галочка подтверждает номер внутри puShok, не паспортную личность и не статус Telegram.
Номер приходит только от доверенного транспорта Bot API после явного запроса.
"""
import calendar
import re
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bot_stars import authorize
from .wallet_ledger import post
from .telegram_environment import environment_name

CONSENT = 'phone-birthday-v1'
MOSCOW = ZoneInfo('Europe/Moscow')


def today():
    return datetime.now(MOSCOW).date()


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Birthday(Input):
    day: int = Field(ge=1, le=31)
    month: int = Field(ge=1, le=12)
    year: int | None = Field(default=None, ge=1900, le=2200)

    @model_validator(mode='after')
    def valid_date(self):
        try:
            value = date(self.year or 2000, self.month, self.day)
        except ValueError:
            raise ValueError('birthday_invalid') from None
        if self.year and (value > today() or today().year-self.year > 120):
            raise ValueError('birthday_invalid')
        return self


class BirthdayUpdate(Input):
    birthday: Birthday | None
    birthday_opt_in: bool


class Consent(Input):
    birthday_opt_in: bool = True


class BotIntent(Consent):
    telegram_user_id: int = Field(gt=0)


class Contact(Input):
    telegram_user_id: int = Field(gt=0)
    chat_id: int = Field(gt=0)
    contact_user_id: int = Field(gt=0)
    private_chat: bool
    forwarded: bool = False
    phone_number: str = Field(min_length=7, max_length=40)
    update_id: int = Field(ge=0)
    message_date: int = Field(gt=0)
    birthday: Birthday | None = None


class Settings(Input):
    congratulations_enabled: bool
    bonus_amount: int = Field(ge=0, le=1000000)


class Finish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notice_id: UUID
    claim_token: UUID
    permanent_failure: bool = False


def private_profile(conn, uid):
    row = conn.execute('''SELECT phone_number,phone_verified_at,birth_day,birth_month,birth_year,
        birthday_source,birthday_opt_in FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s''', (uid, environment_name())).fetchone()
    if not row:
        return dict(phone_number=None, phone_verified_at=None, phone_verified=False,
                    birthday=None, birthday_source=None, birthday_opt_in=False)
    birthday = (dict(day=row.pop('birth_day'), month=row.pop('birth_month'), year=row.pop('birth_year'))
                if row['birth_day'] is not None else None)
    for key in ('birth_day', 'birth_month', 'birth_year'):
        row.pop(key, None)
    return dict(row, birthday=birthday, phone_verified=row['phone_verified_at'] is not None)


def request_contact(conn, uid, opt_in):
    conn.execute('''INSERT INTO phone_verification_requests(user_id,birthday_opt_in,consent_version,telegram_environment)
        VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,telegram_environment) DO UPDATE SET requested_at=now(),
        expires_at=now()+interval '10 minutes',birthday_opt_in=EXCLUDED.birthday_opt_in,
        consent_version=EXCLUDED.consent_version,telegram_environment=EXCLUDED.telegram_environment''', (uid, opt_in, CONSENT, environment_name()))
    return dict(requested=True, expires_in=600, birthday_opt_in=opt_in)


def save_contact(conn, payload):
    if (not payload.private_chat or payload.forwarded or
            payload.chat_id != payload.telegram_user_id or payload.contact_user_id != payload.telegram_user_id):
        raise HTTPException(400, 'own_telegram_contact_required')
    if not re.fullmatch(r'\+?[0-9 ()-]+', payload.phone_number):
        raise HTTPException(400, 'phone_invalid')
    phone = '+' + re.sub(r'[^0-9]', '', payload.phone_number)
    if not re.fullmatch(r'\+[0-9]{7,15}', phone):
        raise HTTPException(400, 'phone_invalid')
    user = conn.execute('SELECT id,blocked_at FROM users WHERE telegram_user_id=%s FOR UPDATE',
                        (payload.telegram_user_id,)).fetchone()
    if not user or user['blocked_at']:
        raise HTTPException(404, 'verification_user_not_found')
    uid = user['id']
    previous = conn.execute('SELECT telegram_update_id FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s', (uid, environment_name())).fetchone()
    if previous and previous['telegram_update_id'] == payload.update_id:
        return dict(verified=True)
    intent = conn.execute('''SELECT *,floor(extract(epoch FROM requested_at))::bigint AS message_after,
        floor(extract(epoch FROM now()))::bigint AS server_time
        FROM phone_verification_requests WHERE user_id=%s AND telegram_environment=%s AND expires_at>now() FOR UPDATE''', (uid, environment_name())).fetchone()
    if (not intent or payload.message_date < intent['message_after'] or
            payload.message_date > intent['server_time']+5):
        raise HTTPException(409, 'verification_request_expired')
    # После недели без обновлений Telegram выбирает update_id случайно.
    # Свежесть доказывается временем сообщения и запросом, а не порядком ID.
    conn.execute('''INSERT INTO verified_profiles(user_id,phone_number,phone_verified_at,telegram_update_id,
        birthday_opt_in,consent_version,consent_at,telegram_environment) VALUES(%s,%s,now(),%s,%s,%s,now(),%s)
        ON CONFLICT(user_id,telegram_environment) DO UPDATE SET phone_number=EXCLUDED.phone_number,phone_verified_at=now(),
        telegram_update_id=EXCLUDED.telegram_update_id,birthday_opt_in=EXCLUDED.birthday_opt_in,
        consent_version=EXCLUDED.consent_version,consent_at=now(),updated_at=now(),telegram_environment=EXCLUDED.telegram_environment''',
        (uid, phone, payload.update_id, intent['birthday_opt_in'], intent['consent_version'], environment_name()))
    if payload.birthday and intent['birthday_opt_in']:
        b = payload.birthday
        conn.execute('''UPDATE verified_profiles SET birth_day=%s,birth_month=%s,birth_year=%s,
            birthday_source='telegram' WHERE user_id=%s AND telegram_environment=%s AND birthday_source IS DISTINCT FROM 'manual' ''',
            (b.day, b.month, b.year, uid, environment_name()))
    conn.execute('DELETE FROM phone_verification_requests WHERE user_id=%s AND telegram_environment=%s', (uid, environment_name()))
    return dict(verified=True)


def generate_birthdays(conn, current=None):
    if environment_name() != 'production':
        return  # Тестовая среда Telegram не создаёт реальные бонусы.
    current = current or today()
    settings = conn.execute('SELECT * FROM birthday_settings WHERE singleton FOR SHARE').fetchone()
    wallet = conn.execute('SELECT enabled FROM wallet_settings WHERE singleton FOR SHARE').fetchone()['enabled']
    amount = settings['bonus_amount'] if wallet else 0
    if not settings['congratulations_enabled'] and not amount:
        return
    leap_day = current.month == 2 and current.day == 28 and not calendar.isleap(current.year)
    rows = conn.execute('''SELECT v.user_id FROM verified_profiles v JOIN users u ON u.id=v.user_id
        WHERE v.phone_verified_at IS NOT NULL AND v.birthday_opt_in AND u.blocked_at IS NULL AND v.telegram_environment=%s
        AND v.birth_month=%s AND (v.birth_day=%s OR (%s AND v.birth_day=29))
        AND NOT EXISTS(SELECT 1 FROM birthday_rewards r WHERE r.user_id=v.user_id AND r.reward_year=%s)
        ORDER BY v.user_id LIMIT 50 FOR UPDATE OF v SKIP LOCKED''',
        (environment_name(), current.month, current.day, leap_day, current.year)).fetchall()
    for row in rows:
        uid = row['user_id']
        reward = conn.execute('''INSERT INTO birthday_rewards(user_id,reward_year,birthday_date,bonus_amount)
            VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,reward_year) DO NOTHING RETURNING id''',
            (uid, current.year, current, amount)).fetchone()
        if not reward:
            continue
        if amount:
            entry = post(conn, uid, 'BONUS', amount, 'gift_credit', f'birthday:{uid}:{current.year}',
                         'Подарок ко дню рождения')
            conn.execute('UPDATE birthday_rewards SET entry_id=%s WHERE id=%s', (entry['id'], reward['id']))
        if settings['congratulations_enabled']:
            conn.execute('INSERT INTO birthday_notice_outbox(reward_id) VALUES(%s)', (reward['id'],))


def claim_birthdays(conn):
    if environment_name() != 'production':
        return []
    generate_birthdays(conn)
    # Не доставлять старые поздравления после отключения пользователем или администратором.
    return conn.execute('''WITH due AS (
        SELECT o.id FROM birthday_notice_outbox o JOIN birthday_rewards r ON r.id=o.reward_id
        JOIN verified_profiles v ON v.user_id=r.user_id JOIN users u ON u.id=r.user_id
        WHERE o.delivered_at IS NULL AND o.abandoned_at IS NULL AND o.next_attempt_at<=now()
        AND (o.lease_until IS NULL OR o.lease_until<now()) AND u.blocked_at IS NULL AND v.birthday_opt_in
        AND v.telegram_environment=%s AND r.birthday_date=(now() AT TIME ZONE 'Europe/Moscow')::date
        AND (SELECT congratulations_enabled FROM birthday_settings WHERE singleton)
        ORDER BY o.next_attempt_at,o.id LIMIT 5 FOR UPDATE OF o SKIP LOCKED
    ), claimed AS (
        UPDATE birthday_notice_outbox o SET claim_token=gen_random_uuid(),lease_until=now()+interval '90 seconds',
        attempts=attempts+1,next_attempt_at=now()+interval '2 minutes'*least(attempts+1,30)
        FROM due WHERE o.id=due.id RETURNING o.*
    ) SELECT c.id AS notice_id,c.claim_token,u.telegram_user_id,u.first_name,r.bonus_amount
        FROM claimed c JOIN birthday_rewards r ON r.id=c.reward_id JOIN users u ON u.id=r.user_id''', (environment_name(),)).fetchall()


def router_for(dsn, current_user, require_admin):
    router = APIRouter(prefix='/api/v1', tags=['verified-profile'])

    def db(action):
        try:
            with psycopg.connect(dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                return action(conn)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    @router.get('/me/verification')
    def mine(response: Response, user=Depends(current_user)):
        response.headers['Cache-Control'] = 'private, no-store'
        return db(lambda conn: private_profile(conn, user['id']))

    @router.post('/me/verification/request')
    def request(payload: Consent, user=Depends(current_user)):
        return db(lambda conn: request_contact(conn, user['id'], payload.birthday_opt_in))

    @router.put('/me/birthday')
    def birthday(payload: BirthdayUpdate, user=Depends(current_user)):
        def save(conn):
            b = payload.birthday
            conn.execute('''INSERT INTO verified_profiles(user_id,birth_day,birth_month,birth_year,birthday_source,
                birthday_opt_in,consent_version,consent_at,telegram_environment) VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s)
                ON CONFLICT(user_id,telegram_environment) DO UPDATE SET birth_day=EXCLUDED.birth_day,birth_month=EXCLUDED.birth_month,
                birth_year=EXCLUDED.birth_year,birthday_source=EXCLUDED.birthday_source,
                birthday_opt_in=EXCLUDED.birthday_opt_in,consent_version=EXCLUDED.consent_version,
                consent_at=now(),updated_at=now()''',
                (user['id'], b.day if b else None, b.month if b else None, b.year if b else None,
                 'manual' if b else None, payload.birthday_opt_in, CONSENT, environment_name()))
            return private_profile(conn, user['id'])
        return db(save)

    @router.get('/admin/birthday-settings')
    def get_settings(response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        return db(lambda conn: conn.execute('SELECT congratulations_enabled,bonus_amount FROM birthday_settings WHERE singleton').fetchone())

    @router.put('/admin/birthday-settings')
    def set_settings(payload: Settings, admin=Depends(require_admin)):
        return db(lambda conn: conn.execute('''UPDATE birthday_settings SET congratulations_enabled=%s,
            bonus_amount=%s,updated_at=now(),updated_by=%s WHERE singleton
            RETURNING congratulations_enabled,bonus_amount''',
            (payload.congratulations_enabled, payload.bonus_amount, admin['id'])).fetchone())

    @router.post('/internal/bot/verification/request')
    def bot_request(payload: BotIntent, response: Response, x_bot_secret: str | None = Header(default=None),
                    x_pushok_telegram_environment: str | None = Header(default=None)):
        authorize(x_bot_secret, response, x_pushok_telegram_environment)
        def create(conn):
            user = conn.execute('SELECT id FROM users WHERE telegram_user_id=%s AND blocked_at IS NULL',
                                (payload.telegram_user_id,)).fetchone()
            if not user:
                raise HTTPException(404, 'verification_user_not_found')
            existing = conn.execute('''SELECT birthday_opt_in FROM phone_verification_requests
                WHERE user_id=%s AND telegram_environment=%s AND expires_at>now()''',
                (user['id'], environment_name())).fetchone()
            return request_contact(conn, user['id'], existing['birthday_opt_in'] if existing else payload.birthday_opt_in)
        return db(create)

    @router.post('/internal/bot/verification/contact')
    def contact(payload: Contact, response: Response, x_bot_secret: str | None = Header(default=None),
                x_pushok_telegram_environment: str | None = Header(default=None)):
        authorize(x_bot_secret, response, x_pushok_telegram_environment)
        return db(lambda conn: save_contact(conn, payload))

    @router.post('/internal/bot/birthday/outbox/claim')
    def claim(response: Response, x_bot_secret: str | None = Header(default=None),
              x_pushok_telegram_environment: str | None = Header(default=None)):
        authorize(x_bot_secret, response, x_pushok_telegram_environment)
        return db(claim_birthdays)

    @router.post('/internal/bot/birthday/outbox/finish')
    def finish(payload: Finish, response: Response, x_bot_secret: str | None = Header(default=None),
               x_pushok_telegram_environment: str | None = Header(default=None)):
        authorize(x_bot_secret, response, x_pushok_telegram_environment)
        def ack(conn):
            column = 'abandoned_at' if payload.permanent_failure else 'delivered_at'
            result = conn.execute(f'''UPDATE birthday_notice_outbox SET {column}=now(),claim_token=NULL,lease_until=NULL
                WHERE id=%s AND claim_token=%s AND lease_until>now() AND delivered_at IS NULL AND abandoned_at IS NULL
                RETURNING id''', (payload.notice_id, payload.claim_token)).fetchone()
            return dict(acknowledged=bool(result))
        return db(ack)
    return router
