"""Атомарная отправка заявки из одной формы. Никаких платежей или обхода модерации."""
import hashlib
import re
from datetime import datetime, timezone

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from .growth import quote


def create_order(dsn, payload, user):
    fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    try:
        with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
            # Сериализуем только повторы одного ключа. Блокировка освобождается с транзакцией.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (str(user["id"]) + ":" + str(payload.request_id),))
            saved = conn.execute("SELECT * FROM order_submissions WHERE user_id=%s AND request_id=%s",
                                 (user["id"], payload.request_id)).fetchone()
            if saved:
                hashes = {fingerprint}
                if not payload.quote_revision and not payload.promo_code:
                    hashes.add(hashlib.sha256(payload.model_dump_json(exclude={'promo_code','quote_revision'}).encode()).hexdigest())
                if saved["request_hash"] not in hashes:
                    raise HTTPException(409, "order_request_conflict")
                return conn.execute("SELECT * FROM campaigns WHERE id=%s AND advertiser_user_id=%s",
                                    (saved["campaign_id"], user["id"])).fetchone()
            # Перечитываем роль под блокировкой: она могла измениться после проверки сессии.
            actor = conn.execute("SELECT role FROM users WHERE id=%s FOR SHARE", (user["id"],)).fetchone()
            tariff = conn.execute("SELECT * FROM tariffs WHERE id=%s AND is_active AND archived_at IS NULL FOR SHARE",
                                  (payload.tariff_id,)).fetchone()
            if not actor or not tariff or (tariff["admin_only"] and actor["role"] != "admin"):
                raise HTTPException(404, "active_tariff_not_found")
            pricing = quote(conn, user, tariff, payload.promo_code, lock=True) if payload.quote_revision else None
            if payload.promo_code and not pricing:
                raise HTTPException(409, 'growth_quote_required')
            if pricing and pricing['revision'] != payload.quote_revision:
                raise HTTPException(409, 'growth_quote_changed')
            final_price = pricing['price_kopecks'] if pricing else tariff['price_kopecks']
            if (final_price != payload.expected_price_kopecks or
                    tariff["duration_seconds"] != payload.expected_duration_seconds):
                raise HTTPException(409, "order_tariff_changed")
            if payload.requested_start_at and payload.requested_start_at <= datetime.now(timezone.utc):
                raise HTTPException(422, "schedule_start_must_be_future")
            if payload.channel_id:
                channel = conn.execute("SELECT id FROM sponsor_channels WHERE id=%s AND owner_user_id=%s AND archived_at IS NULL FOR UPDATE",
                                       (payload.channel_id, user["id"])).fetchone()
                if not channel:
                    raise HTTPException(404, "channel_or_active_tariff_not_found")
            else:
                url = payload.channel.telegram_url.strip().rstrip("/")
                match = re.fullmatch(r"https://(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})", url)
                title = payload.channel.title.strip()
                if not match:
                    raise HTTPException(422, "invalid_telegram_channel_url")
                if not title:
                    raise HTTPException(422, "channel_title_required")
                url = "https://t.me/" + match[1].lower()
                # Не перезаписываем название уже существующего канала и его заявки.
                conn.execute("""INSERT INTO sponsor_channels(owner_user_id,telegram_url,title)
                                VALUES(%s,%s,%s) ON CONFLICT(owner_user_id,telegram_url) DO NOTHING""",
                             (user["id"], url, title))
                channel = conn.execute("SELECT id,archived_at FROM sponsor_channels WHERE owner_user_id=%s AND telegram_url=%s FOR UPDATE",
                                       (user["id"], url)).fetchone()
            if channel.get('archived_at'):
                raise HTTPException(409, 'management_restore_channel')
            campaign = conn.execute("""
                INSERT INTO campaigns(advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                    duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,requested_start_at,
                    moderation_status,pricing_version,base_price_kopecks_snapshot,stars_price_snapshot,
                    growth_offer_id,growth_offer_name,discount_percent_snapshot,purchase_surface,base_stars_price_snapshot)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                """, (user["id"], channel["id"], tariff["id"], tariff["name"], tariff["duration_seconds"],
                      final_price, tariff["currency"], payload.requested_start_at,
                      1 if pricing else None, tariff['price_kopecks'] if pricing else None,
                      pricing['stars_price_xtr'] if pricing else None,
                      pricing['offer_id'] if pricing else None, pricing['offer_name'] if pricing else None,
                      (pricing['discount_percent'] or None) if pricing else None, user.get('login_surface','web'),tariff['stars_price_xtr'] if pricing else None)).fetchone()
            if pricing and pricing['offer_id']:
                conn.execute('INSERT INTO growth_redemptions(campaign_id,offer_id,user_id) VALUES(%s,%s,%s)',
                             (campaign['id'],pricing['offer_id'],user['id']))
            conn.execute("INSERT INTO audit_events(actor_user_id,campaign_id,event_type) VALUES(%s,%s,'campaign_submitted')",
                         (user["id"], campaign["id"]))
            conn.execute("INSERT INTO order_submissions(user_id,request_id,request_hash,campaign_id) VALUES(%s,%s,%s,%s)",
                         (user["id"], payload.request_id, fingerprint, campaign["id"]))
            return campaign
    except psycopg.Error as exc:
        raise HTTPException(503, "database_unavailable") from exc
