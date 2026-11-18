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
from .default_sponsor import fallback_plan
from .campaign_notices import record_failure, FAILURE_CODES

_UNREAD_REPORT = object()


def campaign_automation_status(campaign, confirmed, node=None, *, now=None):
    """Состояние автоматики по серверному времени; календарь не доказывает запуск."""
    now = now or datetime.now(timezone.utc)
    status = campaign['placement_status']
    if status in ('completed', 'cancelled'):
        return status
    if campaign['moderation_status'] != 'approved':
        return 'awaiting_moderation'
    if campaign['payment_status'] != 'verified':
        return 'awaiting_payment'
    if status == 'not_scheduled' and campaign.get('auto_schedule_paused'):
        return 'paused'
    end = campaign.get('scheduled_end_at')
    if end and end <= now:
        return 'finishing' if status == 'active' else 'start_missed'
    if node and (node['status'] != 'active' or node['archived_at']
                 or node['target_engine'] not in (None, 'official_mtproxy')):
        return 'node_unavailable'
    if campaign.get('telegram_binding_pending'):
        return 'preparing_tag'
    if status == 'not_scheduled':
        return 'awaiting_slot'
    if campaign.get('binding_selection_pending'):
        return 'selecting_tag'
    if not campaign['prepared']:
        return 'setup_required'
    start = campaign.get('scheduled_start_at')
    if start and start > now:
        return 'scheduled'
    if confirmed['config_applied']:
        return 'active'
    if not confirmed['agent_recent']:
        return 'agent_unavailable'
    return 'awaiting_confirmation' if status == 'active' else 'starting'


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
    preparation = conn.execute("SELECT phase FROM telegram_binding_jobs WHERE proxy_node_id=%s AND phase<>'done'", (node['id'],)).fetchone()
    if preparation and preparation['phase'] in ('inspect','bind','propagating'):
        return dict(result, reason='telegram_binding_pending')
    # Ручной активный показ не перехватывается автоматикой.
    active = conn.execute("SELECT 1 FROM campaigns c WHERE proxy_node_id=%s AND placement_status='active' AND scheduled_end_at>now() AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied') LIMIT 1", (node['id'],)).fetchone()
    if active:
        return dict(result, reason='manual_placement_active')
    rows = conn.execute("""SELECT c.id,b.ad_tag,c.scheduled_end_at
      FROM campaigns c JOIN channel_promotion_bindings b
      ON b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
      JOIN sponsor_channels ch ON ch.id=c.channel_id
      WHERE c.proxy_node_id=%s
      AND (c.placement_status='active' OR NOT COALESCE(c.auto_schedule_tag_pending,false)) AND (c.placement_status='scheduled' OR (c.placement_status='active' AND EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')))
      AND c.moderation_status='approved' AND c.payment_status='verified'
      AND c.archived_at IS NULL AND ch.archived_at IS NULL AND NOT moderation_channel_blocked(ch)
      AND b.channel_url=ch.telegram_url AND b.node_host=%s
      AND b.node_port=(SELECT port FROM proxy_nodes WHERE id=c.proxy_node_id)
      AND c.scheduled_start_at<=now() AND c.scheduled_end_at>now()
      AND (c.placement_status='active' OR NOT c.auto_schedule_managed OR
           c.auto_start_not_before IS NULL OR c.auto_start_not_before<=now())
      ORDER BY c.scheduled_start_at,c.id LIMIT 2""", (node['id'],node['host'])).fetchall()
    if len(rows) != 1:
        return fallback_plan(conn,node,dict(result, **waiting_reason(conn, node))) if not rows else dict(result, reason='conflicting_campaigns')
    c=rows[0]
    return dict(result,state='apply',reason='ready',campaign_id=str(c['id']),tag=c['ad_tag'],expires_at=c['scheduled_end_at'].timestamp())


def waiting_reason(conn, node):
    row = conn.execute("""SELECT c.id,c.moderation_status,c.payment_status,
        c.auto_schedule_tag_pending AS binding_selection_pending,
        c.scheduled_start_at,c.scheduled_start_at>now() AS future,
        c.scheduled_end_at<=now() AS ended,ch.archived_at AS channel_archived,moderation_channel_blocked(ch) AS channel_blocked,
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
        if row['channel_blocked']:
            return dict(result, reason='channel_moderation_blocked')
        if row['channel_archived']:
            return dict(result, reason='channel_archived')
        if row['moderation_status'] != 'approved':
            return dict(result, reason='moderation_pending')
        if row['payment_status'] != 'verified':
            return dict(result, reason='payment_pending')
        if row['future']:
            return dict(result, reason='waiting_start', next_start_at=row['scheduled_start_at'].isoformat())
        if not row['bound_channel']:
            return dict(result, reason='binding_missing')
        if not row['binding_current']:
            return dict(result, reason='binding_changed')
        if row.get('binding_selection_pending'):
            return dict(result, reason='binding_selection_pending')

    return {'reason': 'no_current_campaign'}


def revision(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()


def confirmation(conn, node, runtime, *, now=None, plan=None, report=_UNREAD_REPORT):
    """Свежий ответ относится только к тому же текущему плану, без публикации тега."""
    now = now or datetime.now(timezone.utc)
    checked_at = runtime.get('checked_at')
    fresh = bool(checked_at and 0 <= (now - checked_at).total_seconds() < 120)
    # Диагностика передаёт один снимок плана и отчёта для всех карточек.
    if report is _UNREAD_REPORT:
        report = read_report(now, node['id'] if node else None)
    recent = bool(node and fresh and report and report['recent']
                  and report.get('node_id') == str(node['id'])
                  and report['checked_at'] >= checked_at)
    applied = False
    if (recent and report['code'] == 'tag_applied'
            and report.get('plan_revision') == runtime.get('plan_revision')
            and runtime.get('state') == 'applied' and runtime.get('plan_revision')):
        if plan is None:
            plan = node_plan(conn, node)
        applied = (plan['state'] in ('apply','default')
                   and str(plan.get('campaign_id')) == str(runtime.get('campaign_id'))
                   and revision(plan) == runtime['plan_revision'])
    return {'config_applied': applied, 'agent_recent': recent,
            'checked_at': checked_at, 'display_verified': False}


def handle(conn, request):
    if str(request.get('action', '')).startswith('owner_'):
        from .telegram_binding import handle as owner_handle
        return owner_handle(conn, request)
    plan=desired(conn,request['host'],int(request['port']),request.get('resolved_host'))
    if request.get('action')=='failure':
        if plan.get('node_id') and request.get('code') in FAILURE_CODES:
            record_failure(conn,plan['node_id'],request['code'])
            return {'recorded':True}
        return {'recorded':False}
    if request.get('action')=='ack':
        if plan.get('node_id'):
            # Смена расписания и отмена не должны обгонять подтверждение агента.
            conn.execute("SELECT id FROM campaigns WHERE proxy_node_id=%s AND placement_status IN ('scheduled','active') FOR UPDATE", (plan['node_id'],))
            plan=desired(conn,request['host'],int(request['port']),request.get('resolved_host'))
        if revision(plan)!=request.get('revision'):
            return {'accepted':False}
        if plan.get('node_id'):
            conn.execute("""INSERT INTO promotion_runtime(proxy_node_id,campaign_id,state,plan_revision)
              VALUES(%s,%s,%s,%s) ON CONFLICT(proxy_node_id) DO UPDATE SET
              campaign_id=EXCLUDED.campaign_id,state=EXCLUDED.state,
              plan_revision=EXCLUDED.plan_revision,checked_at=now()""",
              (plan['node_id'],plan.get('campaign_id'),'applied' if plan['state'] in ('apply','default') else 'idle',revision(plan)))
        # Успешный idle ACK означает восстановление исходного тега; apply ACK —
        # переход к следующей кампании. Ошибка apply/restore не отправляет ACK.
        if plan.get('node_id'):
            completed = conn.execute("""UPDATE campaigns c SET placement_status='completed',updated_at=now()
                WHERE c.proxy_node_id=%s AND c.placement_status='active'
                  AND c.scheduled_end_at<=now()
                RETURNING c.id""", (plan['node_id'],)).fetchall()
            for campaign in completed:
                conn.execute("""INSERT INTO audit_events(campaign_id,event_type,details_json)
                    VALUES(%s,'placement_completed_by_agent',
                    '{"source":"promotion_agent","display_verified":false}'::jsonb)""", (campaign['id'],))
        # Сначала освобождаем уникальное активное размещение на узле.
        if plan['state']=='apply':
            # ACK приходит только после применения тега и проверки работающего процесса.
            # Это подтверждение запуска, а не измерение показов Telegram.
            activated = conn.execute("""UPDATE campaigns
                SET placement_status='active', updated_at=now()
                WHERE id=%s AND proxy_node_id=%s AND placement_status='scheduled'
                  AND moderation_status='approved' AND payment_status='verified'
                  AND archived_at IS NULL
                  AND scheduled_start_at<=now() AND scheduled_end_at>now()
                RETURNING id""", (plan['campaign_id'],plan['node_id'])).fetchone()
            if activated:
                conn.execute("""INSERT INTO audit_events(campaign_id,event_type,details_json)
                    VALUES(%s,'placement_activated_by_agent',
                           '{"source":"promotion_agent","display_verified":false}'::jsonb)""",
                    (activated['id'],))
        if plan.get('node_id') and (plan['state'] in ('apply','default') or plan.get('reason') in ('period_ended','waiting_start','no_current_campaign')):
            conn.execute('UPDATE promotion_incidents SET resolved_at=now() WHERE proxy_node_id=%s AND resolved_at IS NULL',(plan['node_id'],))
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

