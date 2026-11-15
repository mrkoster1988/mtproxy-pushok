"""Host-only протокол для подготовленных тегов. Не подтверждает наблюдение показа."""
import json
import os
import sys
import hashlib
import ipaddress
from datetime import datetime, timezone
import psycopg
from psycopg.rows import dict_row
from .promotion_report import read_report

_UNREAD_REPORT = object()


def desired(conn, host, port, resolved_host=None):
    hosts = [host]
    if resolved_host is not None:
        # Дополнительный адрес приходит только от локального агента, без HTTP.
        address = str(ipaddress.IPv4Address(resolved_host))
        if address not in hosts:
            hosts.append(address)
    nodes = conn.execute(
        "SELECT id,host,status,archived_at,target_engine FROM proxy_nodes WHERE host=ANY(%s) AND port=%s",
        (hosts,port),
    ).fetchall()
    # Два имени одного адреса могут иметь разные кампании: не угадываем узел.
    if len(nodes) != 1:
        return {"state":"idle", "reason":"node_not_found" if not nodes else "node_ambiguous"}
    return node_plan(conn, nodes[0])


def node_plan(conn, node):
    """Общий план для агента и диагностики; сам ничего не применяет."""
    result = {"state":"idle", "node_id":str(node['id'])}
    if node['archived_at']:
        return dict(result, reason='node_archived')
    if node['status'] != 'active':
        return dict(result, reason='node_paused')
    if node['target_engine'] not in (None,'official_mtproxy'):
        return dict(result, reason='engine_unsupported')
    # Ручной активный показ не перехватывается автоматикой.
    active = conn.execute("SELECT 1 FROM campaigns c WHERE proxy_node_id=%s AND placement_status='active' AND scheduled_end_at>now() AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied') LIMIT 1", (node['id'],)).fetchone()
    if active:
        return dict(result, reason='manual_placement_active')
    rows = conn.execute("""SELECT c.id,b.ad_tag,c.scheduled_end_at
      FROM campaigns c JOIN channel_promotion_bindings b
      ON b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
      JOIN sponsor_channels ch ON ch.id=c.channel_id
      WHERE c.proxy_node_id=%s AND (c.placement_status='scheduled' OR (c.placement_status='active' AND EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')))
      AND c.moderation_status='approved' AND c.payment_status='verified'
      AND c.archived_at IS NULL AND ch.archived_at IS NULL
      AND b.channel_url=ch.telegram_url AND b.node_host=%s
      AND b.node_port=(SELECT port FROM proxy_nodes WHERE id=c.proxy_node_id)
      AND c.scheduled_start_at<=now() AND c.scheduled_end_at>now()
      ORDER BY c.scheduled_start_at,c.id LIMIT 2""", (node['id'],node['host'])).fetchall()
    if len(rows) != 1:
        return dict(result, **waiting_reason(conn, node)) if not rows else dict(result, reason='conflicting_campaigns')
    c=rows[0]
    return dict(result,state='apply',reason='ready',campaign_id=str(c['id']),tag=c['ad_tag'],expires_at=c['scheduled_end_at'].timestamp())


def waiting_reason(conn, node):
    row = conn.execute("""SELECT c.id,c.moderation_status,c.payment_status,
        c.scheduled_start_at,c.scheduled_start_at>now() AS future,
        c.scheduled_end_at<=now() AS ended,ch.archived_at AS channel_archived,
        b.channel_id AS bound_channel,
        (b.channel_url=ch.telegram_url AND b.node_host=n.host AND b.node_port=n.port) AS binding_current
        FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id
        JOIN proxy_nodes n ON n.id=c.proxy_node_id
        LEFT JOIN channel_promotion_bindings b ON b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
        WHERE c.proxy_node_id=%s AND c.archived_at IS NULL AND c.placement_status IN ('scheduled','active')
        ORDER BY (c.scheduled_end_at>now()) DESC,c.scheduled_start_at,c.id LIMIT 1""", (node['id'],)).fetchone()
    if row:
        result = {'campaign_id': str(row['id'])}
        if row['ended']:
            return dict(result, reason='period_ended')
        if row['channel_archived']:
            return dict(result, reason='channel_archived')
        if row['moderation_status'] != 'approved':
            return dict(result, reason='moderation_pending')
        if row['payment_status'] != 'verified':
            return dict(result, reason='payment_pending')
        if not row['bound_channel']:
            return dict(result, reason='binding_missing')
        if not row['binding_current']:
            return dict(result, reason='binding_changed')
        if row['future']:
            return dict(result, reason='waiting_start', next_start_at=row['scheduled_start_at'].isoformat())
    binding = conn.execute('SELECT 1 FROM channel_promotion_bindings WHERE proxy_node_id=%s LIMIT 1', (node['id'],)).fetchone()
    return {'reason': 'no_current_campaign' if binding else 'binding_missing'}


def revision(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()


def confirmation(conn, node, runtime, *, now=None, plan=None, report=_UNREAD_REPORT):
    """Свежий ответ относится только к тому же текущему плану, без публикации тега."""
    now = now or datetime.now(timezone.utc)
    checked_at = runtime.get('checked_at')
    fresh = bool(checked_at and 0 <= (now - checked_at).total_seconds() < 120)
    # Диагностика передаёт один снимок плана и отчёта для всех карточек.
    if report is _UNREAD_REPORT:
        report = read_report(now)
    recent = bool(node and fresh and report and report['recent']
                  and report.get('node_id') == str(node['id'])
                  and report['checked_at'] >= checked_at)
    applied = False
    if (recent and report['code'] == 'tag_applied'
            and report.get('plan_revision') == runtime.get('plan_revision')
            and runtime.get('state') == 'applied' and runtime.get('plan_revision')):
        if plan is None:
            plan = node_plan(conn, node)
        applied = (plan['state'] == 'apply'
                   and plan.get('campaign_id') == str(runtime.get('campaign_id'))
                   and revision(plan) == runtime['plan_revision'])
    return {'config_applied': applied, 'agent_recent': recent,
            'checked_at': checked_at, 'display_verified': False}


def handle(conn, request):
    plan=desired(conn,request['host'],int(request['port']),request.get('resolved_host'))
    if request.get('action')=='ack':
        if revision(plan)!=request.get('revision'):
            return {'accepted':False}
        if plan.get('node_id'):
            conn.execute("""INSERT INTO promotion_runtime(proxy_node_id,campaign_id,state,plan_revision)
              VALUES(%s,%s,%s,%s) ON CONFLICT(proxy_node_id) DO UPDATE SET
              campaign_id=EXCLUDED.campaign_id,state=EXCLUDED.state,
              plan_revision=EXCLUDED.plan_revision,checked_at=now()""",
              (plan['node_id'],plan.get('campaign_id'),'applied' if plan['state']=='apply' else 'idle',revision(plan)))
        return {'accepted':True}
    return dict(plan,revision=revision(plan))


def main():
    # Только stdin/stdout контейнера, без HTTP и публикации тегов в журнале.
    request=json.loads(sys.stdin.read(8192))
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=3,row_factory=dict_row,
                         options='-c statement_timeout=5000') as conn:
        result=handle(conn,request)
    print(json.dumps(result))

if __name__=='__main__':
    try:
        main()
    except Exception:
        print('{"error":"promotion_runtime_unavailable"}')
        sys.exit(1)

