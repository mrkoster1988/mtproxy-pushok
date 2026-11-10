"""Один безопасный проход обслуживания MTProxy Pushok.

По умолчанию только отчёт (dry run). --apply удаляет просроченные холды,
но НИКОГДА не завершает активную кампанию и не управляет @MTProxyBot.
Работает отдельно от API и может вызываться cron/systemd timer.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row


def service_once(conn, *, apply: bool = False, limit: int = 100) -> dict:
    """Согласованный с блокировками БД, идемпотентный проход."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid_limit")
    # Используем транзакционную блокировку: только один worker на БД.
    locked = conn.execute(
        "SELECT pg_try_advisory_xact_lock(782341, 101) AS acquired"
    ).fetchone()
    if not locked["acquired"]:
        return {"status": "busy", "expired_holds": 0,
                "deleted_holds": 0, "overdue_campaign_ids": []}
    # Считаем по времени самой БД. Блокировка строк позволяет не удалять
    # холд, который прямо сейчас обрабатывается другим запросом.
    holds = conn.execute(
        """SELECT id FROM slot_holds
           WHERE expires_at <= now()
           ORDER BY expires_at, id
           LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,)
    ).fetchall()
    ids = [row["id"] for row in holds]
    removed = 0
    if apply and ids:
        result = conn.execute("DELETE FROM slot_holds WHERE id = ANY(%s)", (ids,))
        removed = result.rowcount
    # Статус "active" не меняется — это запрос оператору проверить
    # реальное выключение promotion в Telegram.
    overdue = conn.execute(
        """SELECT id FROM campaigns
           WHERE placement_status = 'active'
             AND scheduled_end_at <= now()
           ORDER BY scheduled_end_at, id LIMIT %s""", (limit,)
    ).fetchall()
    return {
        "status": "ok",
        "dry_run": not apply,
        "expired_holds": len(ids),
        "deleted_holds": removed,
        "overdue_campaign_ids": [str(row["id"]) for row in overdue],
        "overdue_requires_manual_verification": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверка холдов и завершений MTProxy")
    parser.add_argument("--apply", action="store_true",
                        help="Удалить истёкшие холды; НЕ завершает кампании")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit должен быть от 1 до 1000")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL не задан")
    try:
        with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
            data = service_once(conn, apply=args.apply, limit=args.limit)
    except psycopg.Error:
        print(json.dumps({"status": "database_unavailable"}))
        return 1
    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(data, ensure_ascii=False))
    return 0 if data["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
