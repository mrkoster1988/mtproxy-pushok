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
            candidates = []
            for node in nodes:
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
            conn.execute("INSERT INTO audit_events(campaign_id,event_type,details_json) VALUES(%s,'placement_auto_scheduled',%s::jsonb)",
                         (c['id'],json.dumps({'node_id':node_id,'start_at':at.isoformat(),'end_at':(at+duration).isoformat()})))
            scheduled += 1
    return {'scheduled': scheduled}
