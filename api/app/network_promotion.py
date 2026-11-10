"""План сети MTProxy: оплаченные слоты важнее самопродвижения.

Никаких SSH, Docker, @MTProxyBot и платежей. Результат — план для оператора,
а не доказательство действующего спонсора или автоматическая смена рекламы.
"""
import os
from datetime import datetime, timezone

import psycopg
from fastapi import Depends, HTTPException
from psycopg.rows import dict_row

from .sponsor_planner import parse_public_channel


def build_network_plan(rows, *, at, own_channel_url):
    """Построить безопасный план: коммерческий слот > собственный канал.

    SQL уже отбирает размещения на конкретное время. Дополнительная проверка
    пересечений здесь защищает от ошибочных данных и не выбирает случайного
    победителя, если ограничения базы отключены.
    """
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("network_plan_timezone_required")
    if parse_public_channel(own_channel_url) is None:
        raise ValueError("invalid_official_channel_url")

    nodes = {}
    for row in rows:
        key = str(row["node_id"])
        if key not in nodes:
            nodes[key] = {
                "node_id": key,
                "name": row["node_name"],
                "node_status": row["node_status"],
                "target_engine": row.get("target_engine"),
                "tag_registered": bool(row["tag_registered"]),
                "_campaigns": {},
            }
        if row.get("campaign_id") is not None:
            nodes[key]["_campaigns"][str(row["campaign_id"])] = {
                "campaign_id": str(row["campaign_id"]),
                "channel_url": row["campaign_channel_url"],
                "channel_verified": row.get("telegram_channel_id") is not None,
                "moderation_status": row["moderation_status"],
                "payment_status": row["payment_status"],
                "placement_status": row["placement_status"],
            }

    result = []
    for node in nodes.values():
        campaigns = list(node.pop("_campaigns").values())
        campaign = campaigns[0] if len(campaigns) == 1 else None
        node["campaign_id"] = campaign["campaign_id"] if campaign else None
        node["target_channel_url"] = own_channel_url if not campaigns else None
        node["channel_kind"] = "paid" if campaign else ("own" if not campaigns else "conflict")
        if len(campaigns) > 1:
            state, action = "overlap_conflict", "investigate_conflicting_placements"
        elif node["node_status"] != "active":
            state, action = "node_paused", "inspect_node_before_any_sponsor"
        elif not node["tag_registered"]:
            state, action = "tag_registration_required", "register_proxy_with_mtproxybot"
        elif campaign and (campaign["moderation_status"] != "approved" or campaign["payment_status"] != "verified"
                           or not campaign["channel_verified"]
                           or parse_public_channel(campaign["channel_url"]) is None):
            state, action = "paid_placement_not_approved", "resolve_moderation_or_payment"
        elif campaign:
            state, action = "paid_channel_planned", "manually_verify_paid_sponsor"
            node["target_channel_url"] = campaign["channel_url"]
        else:
            state, action = "own_channel_planned", "manually_set_own_sponsor"

        node["state"] = state
        node["operator_action"] = action
        node["display_verified"] = False
        node["automatic_switch_supported"] = False
        node["changes_applied"] = False
        result.append(node)

    counts = {key: 0 for key in (
        "own_channel_planned", "paid_channel_planned",
        "paid_placement_not_approved", "tag_registration_required",
        "node_paused", "overlap_conflict"
    )}
    for node in result:
        counts[node["state"]] += 1
    return {
        "planned_for": at.isoformat(),
        "official_channel_url": own_channel_url,
        "priority": "paid_confirmed_slot_then_own_channel",
        "node_count": len(result),
        "summary": counts,
        "nodes": result,
        "changes_applied": False,
        "telegram_sponsor_verified": False,
        "automatic_switch_supported": False,
        "metrics_are_estimates": False,
        "instructions": [
            "В @MTProxyBot зарегистрировать каждый узел, получить tag и назначить публичный канал.",
            "Для коммерческого слота вручную изменить Set promotion и проверить показ отдельным неподписанным клиентом.",
            "После окончания платного слота вручную восстановить собственный канал и проверить его отображение.",
            "Пока Telegram не подтвердил показ, не считать запланированный канал реально показанным.",
        ],
    }


def register_network_routes(app, require_admin, database_url):
    """Добавить read-only admin API к существующей FastAPI без нового сервиса."""
    @app.get("/api/v1/admin/network/promotion-plan", tags=["admin"])
    def network_promotion_plan(
        at: datetime | None = None,
        admin: dict = Depends(require_admin),
    ):
        when = at or datetime.now(timezone.utc)
        if when.tzinfo is None or when.utcoffset() is None:
            raise HTTPException(status_code=422, detail="network_plan_timezone_required")
        own = os.getenv("OFFICIAL_AD_CHANNEL_URL", "https://t.me/pushok_proxy")
        if parse_public_channel(own) is None:
            raise HTTPException(status_code=503, detail="official_channel_not_configured")
        try:
            with psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row) as connection:
                rows = connection.execute(
                    """
                    SELECT n.id AS node_id, n.name AS node_name,
                           n.status AS node_status, n.target_engine,
                           (r.proxy_node_id IS NOT NULL) AS tag_registered,
                           c.id AS campaign_id,
                           c.moderation_status, c.payment_status, c.placement_status,
                           sc.telegram_url AS campaign_channel_url,
                           sc.telegram_channel_id
                    FROM proxy_nodes AS n
                    LEFT JOIN proxy_sponsor_registrations AS r ON r.proxy_node_id = n.id
                    LEFT JOIN campaigns AS c ON c.proxy_node_id = n.id
                        AND c.placement_status IN ('scheduled', 'active')
                        AND c.scheduled_start_at <= %s
                        AND c.scheduled_end_at > %s
                    LEFT JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                    ORDER BY n.name, n.id
                    """,
                    (when, when),
                ).fetchall()
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="database_unavailable") from exc
        plan = build_network_plan(rows, at=when, own_channel_url=own)
        from fastapi import Response
        return Response(
            content=__import__("json").dumps(plan, ensure_ascii=False),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
