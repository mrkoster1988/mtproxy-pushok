"""Назначение свободного периода. Не управляет тегами и не подтверждает показ."""
from datetime import datetime, timedelta, timezone
import json
import psycopg
from psycopg.rows import dict_row


def earliest_gap(start, duration, occupied):
    for left, right in sorted(occupied):
        if right <= start:
            continue
        if start + duration <= left:
            break
        start = max(start, right)
    return start


def service_once(dsn, limit=20):
    scheduled = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(736102482) AS ok').fetchone()['ok']:
            return {'scheduled': 0}
        # После простоя не списываем пропущенный период как оказанную услугу.
        # Восстановление начинается только после свежего ACK работающего агента.
        missed = conn.execute("""SELECT c.* FROM campaigns c
            WHERE c.archived_at IS NULL AND c.moderation_status='approved'
              AND c.payment_status='verified' AND c.placement_status='scheduled'
              AND c.scheduled_end_at<=now() AND NOT c.auto_schedule_paused
              AND EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.proxy_node_id=c.proxy_node_id
                  AND r.checked_at>now()-interval '2 minutes' AND r.checked_at<=now())
            ORDER BY c.scheduled_start_at,c.id LIMIT %s FOR UPDATE OF c SKIP LOCKED""", (limit,)).fetchall()
        recovered = {c['id']: c for c in missed}
        for c in missed:
            conn.execute("""UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
                scheduled_start_at=NULL,scheduled_end_at=NULL,updated_at=now() WHERE id=%s""", (c['id'],))
        campaigns = conn.execute("""SELECT * FROM campaigns WHERE archived_at IS NULL
            AND moderation_status='approved' AND payment_status='verified'
            AND placement_status='not_scheduled' AND NOT auto_schedule_paused
            AND (requested_start_at IS NULL OR requested_start_at<=now()+interval '90 days')
            ORDER BY created_at,id LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,)).fetchall()
        nodes = conn.execute("SELECT id FROM proxy_nodes WHERE status='active' AND archived_at IS NULL ORDER BY id FOR UPDATE").fetchall()
        now = datetime.now(timezone.utc) + timedelta(minutes=2)
        for c in campaigns:
            start = max(c['requested_start_at'] or now, now)
            duration = timedelta(seconds=c['duration_seconds_snapshot'])
            prepared = conn.execute("""SELECT b.proxy_node_id FROM channel_promotion_bindings b
                JOIN proxy_nodes n ON n.id=b.proxy_node_id
                JOIN sponsor_channels ch ON ch.id=b.channel_id
                WHERE b.channel_id=%s AND n.status='active' AND n.archived_at IS NULL
                AND ch.archived_at IS NULL AND b.channel_url=ch.telegram_url
                AND b.node_host=n.host AND b.node_port=n.port
                AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')""",(c['channel_id'],)).fetchall()
            prepared_ids = {str(p['proxy_node_id']) for p in prepared}
            candidates = []
            for node in nodes:
                if str(node["id"]) not in prepared_ids:
                    continue
                rows = conn.execute("""SELECT scheduled_start_at AS left_at,scheduled_end_at AS right_at
                    FROM campaigns WHERE proxy_node_id=%s AND placement_status IN ('scheduled','active')
                    UNION ALL SELECT starts_at,ends_at FROM slot_holds
                    WHERE proxy_node_id=%s AND campaign_id<>%s AND expires_at>now()""",
                    (node['id'],node['id'],c['id'])).fetchall()
                available = earliest_gap(start,duration,[(r['left_at'],r['right_at']) for r in rows])
                if available <= now + timedelta(days=90):
                    candidates.append((available,str(node['id'])))
            if not candidates:
                continue
            at,node_id = min(candidates)
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s',(c['id'],))
            conn.execute("""UPDATE campaigns SET proxy_node_id=%s,scheduled_start_at=%s,
                scheduled_end_at=%s,placement_status='scheduled',updated_at=now() WHERE id=%s""",
                (node_id,at,at+duration,c['id']))
            details = {'node_id':node_id,'start_at':at.isoformat(),'end_at':(at+duration).isoformat()}
            if c['id'] in recovered:
                previous = recovered[c['id']]
                details.update(reason='missed_start_recovered',previous_start_at=previous['scheduled_start_at'].isoformat(),
                               previous_end_at=previous['scheduled_end_at'].isoformat())
            conn.execute("INSERT INTO audit_events(campaign_id,event_type,details_json) VALUES(%s,'placement_auto_scheduled',%s::jsonb)",
                         (c['id'],json.dumps(details)))
            scheduled += 1
    return {'scheduled': scheduled}
