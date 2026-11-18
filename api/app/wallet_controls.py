"""Единый выключатель кошелька. Подтверждения провайдеров его не используют."""
from fastapi import HTTPException


def settings(conn, *, lock=False):
    return conn.execute('SELECT enabled,bonus_spending_enabled,updated_at FROM wallet_settings WHERE singleton'+
                        (' FOR SHARE' if lock else '')).fetchone()


def require_enabled(conn):
    policy=settings(conn,lock=True)
    if not policy['enabled']:
        raise HTTPException(409,'wallet_disabled')
    return policy
