"""Атомарная отправка заявки из одной формы. Никаких платежей или обхода модерации."""
import hashlib
import re
from datetime import datetime, timezone

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row


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
                if saved["request_hash"] != fingerprint:
                    raise HTTPException(409, "order_request_conflict")
                return conn.execute("SELECT * FROM campaigns WHERE id=%s AND advertiser_user_id=%s",
                                    (saved["campaign_id"], user["id"])).fetchone()
            # Перечитываем роль под блокировкой: она могла измениться после проверки сессии.
            actor = conn.execute("SELECT role FROM users WHERE id=%s FOR SHARE", (user["id"],)).fetchone()
            tariff = conn.execute("SELECT * FROM tariffs WHERE id=%s AND is_active FOR SHARE",
                                  (payload.tariff_id,)).fetchone()
            if not actor or not tariff or (tariff["admin_only"] and actor["role"] != "admin"):
                raise HTTPException(404, "active_tariff_not_found")
            if (tariff["price_kopecks"] != payload.expected_price_kopecks or
                    tariff["duration_seconds"] != payload.expected_duration_seconds):
                raise HTTPException(409, "order_tariff_changed")
            if payload.requested_start_at and payload.requested_start_at <= datetime.now(timezone.utc):
                raise HTTPException(422, "schedule_start_must_be_future")
            if payload.channel_id:
                channel = conn.execute("SELECT id FROM sponsor_channels WHERE id=%s AND owner_user_id=%s FOR UPDATE",
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
                channel = conn.execute("SELECT id FROM sponsor_channels WHERE owner_user_id=%s AND telegram_url=%s FOR UPDATE",
                                       (user["id"], url)).fetchone()
            campaign = conn.execute("""
                INSERT INTO campaigns(advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                    duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,requested_start_at,
                    moderation_status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *
                """, (user["id"], channel["id"], tariff["id"], tariff["name"], tariff["duration_seconds"],
                      tariff["price_kopecks"], tariff["currency"], payload.requested_start_at)).fetchone()
            conn.execute("INSERT INTO audit_events(actor_user_id,campaign_id,event_type) VALUES(%s,%s,'campaign_submitted')",
                         (user["id"], campaign["id"]))
            conn.execute("INSERT INTO order_submissions(user_id,request_id,request_hash,campaign_id) VALUES(%s,%s,%s,%s)",
                         (user["id"], payload.request_id, fingerprint, campaign["id"]))
            return campaign
    except psycopg.Error as exc:
        raise HTTPException(503, "database_unavailable") from exc
