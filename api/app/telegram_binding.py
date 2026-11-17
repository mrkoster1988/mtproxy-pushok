"""Host-only очередь внешней привязки. Не HTTP API и не подтверждение показов."""
import re
from datetime import datetime, timedelta, timezone
from .auto_schedule import earliest_gap

ERRORS = {
    'not_connected', 'revoked', 'connection_busy', 'account_mismatch', 'unexpected_mtproxy_bot',
    'public_channel_required', 'proxy_identity_not_verified', 'tag_not_verified',
    'proxy_not_found', 'proxy_ambiguous', 'proxy_list_loop', 'proxy_list_ambiguous',
    'proxy_list_too_long', 'ambiguous_bot_response', 'bot_response_timeout', 'tag_changed',
    'set_promotion_button_missing', 'channel_prompt_not_verified', 'binding_not_verified',
    'telegram_unavailable', 'telegram_flood_wait', 'owner_helper_unavailable', 'target_changed',
    'tag_used_by_other_node', 'node_busy',
}


def resolve(conn, request):
    hosts = [request['host']]
    if request.get('resolved_host'):
        import ipaddress
        hosts.append(str(ipaddress.IPv4Address(request['resolved_host'])))
    nodes = conn.execute('SELECT * FROM proxy_nodes WHERE host=ANY(%s) AND port=%s FOR UPDATE',
                         (hosts, int(request['port']))).fetchall()
    return nodes[0] if len(nodes) == 1 else None


def eligible(conn, job, node):
    if (node['status'] != 'active' or node['archived_at'] or node['target_engine'] not in (None, 'official_mtproxy')
            or (job['node_host'], job['node_port']) != (node['host'], node['port'])):
        return False
    if job['campaign_id'] is None:
        return bool(conn.execute('''SELECT 1 FROM default_sponsor_settings s
            JOIN sponsor_channels ch ON ch.id=s.channel_id WHERE s.singleton=true
            AND ch.id=%s AND ch.telegram_url=%s AND ch.archived_at IS NULL''',
            (job['channel_id'],job['channel_url'])).fetchone())
    return bool(conn.execute('''SELECT 1 FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id
        WHERE c.id=%s AND c.channel_id=%s AND ch.telegram_url=%s
          AND c.archived_at IS NULL AND ch.archived_at IS NULL
          AND c.moderation_status='approved' AND c.payment_status='verified'
          AND NOT c.auto_schedule_paused AND c.placement_status IN ('not_scheduled','scheduled')
          AND (c.placement_status='not_scheduled' OR c.auto_schedule_managed)
          AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
          AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')''',
        (job['campaign_id'], job['channel_id'], job['channel_url'])).fetchone())


def busy(conn, node_id):
    # Ручные слоты не перехватываем даже задолго до их начала. Подготовленному
    # запуску нужен запас на распространение нового канала в Telegram.
    return bool(conn.execute('''SELECT 1 FROM campaigns c WHERE proxy_node_id=%s AND
        (placement_status='active' OR (placement_status='scheduled' AND
          (NOT auto_schedule_managed OR (scheduled_end_at>now() AND scheduled_start_at<now()+interval '2 hours'
           AND EXISTS(SELECT 1 FROM channel_promotion_bindings b
             JOIN sponsor_channels ch ON ch.id=b.channel_id JOIN proxy_nodes n ON n.id=b.proxy_node_id
             WHERE b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
             AND b.channel_url=ch.telegram_url AND (b.node_host,b.node_port)=(n.host,n.port))))))
        UNION ALL SELECT 1 FROM slot_holds WHERE proxy_node_id=%s AND expires_at>now()
          AND ends_at>now() AND starts_at<now()+interval '2 hours' LIMIT 1''', (node_id,node_id)).fetchone())


def public_status(conn):
    return conn.execute('''SELECT j.proxy_node_id,n.name,j.campaign_id,j.phase,j.retry_at,
        j.verified_at,j.error_code,j.updated_at FROM telegram_binding_jobs j
        JOIN proxy_nodes n ON n.id=j.proxy_node_id WHERE n.archived_at IS NULL ORDER BY n.name''').fetchall()


def preparation_improves_slot(conn, campaign, node_id):
    """Не готовим новый тег, если существующая связь даёт более ранний полный слот."""
    now = datetime.now(timezone.utc)
    start = max(now, campaign['requested_start_at'] or now)
    duration = timedelta(seconds=campaign['duration_seconds_snapshot'])
    def available(node, after):
        occupied = conn.execute('''SELECT scheduled_start_at AS left_at,scheduled_end_at AS right_at
            FROM campaigns WHERE proxy_node_id=%s AND id<>%s AND placement_status IN ('scheduled','active')
            UNION ALL SELECT starts_at,ends_at FROM slot_holds
            WHERE proxy_node_id=%s AND campaign_id<>%s AND expires_at>now()''',
            (node,campaign['id'],node,campaign['id'])).fetchall()
        return earliest_gap(after,duration,[(r['left_at'],r['right_at']) for r in occupied])
    after_preparation = available(node_id,max(start,now+timedelta(hours=1,minutes=2)))
    prepared = conn.execute('''SELECT n.id FROM channel_promotion_bindings b JOIN proxy_nodes n ON n.id=b.proxy_node_id
        WHERE b.channel_id=%s AND b.channel_url=%s AND (b.node_host,b.node_port)=(n.host,n.port)
          AND n.status='active' AND n.archived_at IS NULL AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')
          AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.proxy_node_id=n.id AND j.phase<>'done')''',
        (campaign['channel_id'],campaign['telegram_url'])).fetchall()
    return not any(available(n['id'],start) <= after_preparation for n in prepared)


def handle(conn, request):
    # Та же блокировка, что у планировщика; короткие транзакции, без Telegram внутри.
    if not conn.execute('SELECT pg_try_advisory_xact_lock(736102482) AS ok').fetchone()['ok']:
        return {'state': 'wait'}
    node = resolve(conn, request)
    if not node:
        return {'state': 'wait'}
    job = conn.execute('SELECT *,retry_at<=now() AS due FROM telegram_binding_jobs WHERE proxy_node_id=%s FOR UPDATE',
                       (node['id'],)).fetchone()
    action = request['action']
    if action == 'owner_claim':
        if job and job['phase'] != 'done':
            if not eligible(conn, job, node):
                # Отмена/изменение не даёт старому ответу создать новую связь.
                conn.execute("UPDATE telegram_binding_jobs SET phase='done',error_code='target_changed',updated_at=now() WHERE id=%s", (job['id'],))
                return {'state': 'wait'}
            if not job['due']:
                return {'state': 'wait'}
        else:
            if node['status'] != 'active' or node['archived_at'] or node['target_engine'] not in (None, 'official_mtproxy') or busy(conn, node['id']):
                return {'state': 'wait'}
            candidates = conn.execute('''SELECT c.id,c.channel_id,ch.telegram_url,c.requested_start_at,c.duration_seconds_snapshot FROM campaigns c
                JOIN sponsor_channels ch ON ch.id=c.channel_id
                WHERE c.moderation_status='approved' AND c.payment_status='verified'
                  AND c.archived_at IS NULL AND ch.archived_at IS NULL AND NOT c.auto_schedule_paused
                  AND c.placement_status IN ('not_scheduled','scheduled')
                  AND (c.placement_status='not_scheduled' OR c.auto_schedule_managed)
                  AND COALESCE(c.requested_start_at,now())<=now()+interval '1 hour'
                  AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
                  AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')
                  AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.campaign_id=c.id AND j.phase<>'done')
                ORDER BY COALESCE(c.requested_start_at,c.created_at),c.created_at,c.id
                LIMIT 20 FOR UPDATE OF c SKIP LOCKED''').fetchall()
            candidate = next((c for c in candidates if preparation_improves_slot(conn,c,node['id'])),None)
            if not candidate:
                candidate = conn.execute('''SELECT NULL AS id,ch.id AS channel_id,ch.telegram_url
                    FROM default_sponsor_settings s JOIN sponsor_channels ch ON ch.id=s.channel_id
                    WHERE s.singleton=true AND ch.archived_at IS NULL AND NOT EXISTS(
                      SELECT 1 FROM channel_promotion_bindings b WHERE b.channel_id=ch.id
                        AND b.proxy_node_id=%s AND b.channel_url=ch.telegram_url
                        AND (b.node_host,b.node_port)=(%s,%s))''', (node['id'],node['host'],node['port'])).fetchone()
                if not candidate:
                    return {'state': 'wait'}
            conn.execute('UPDATE campaigns SET auto_schedule_managed=true,auto_schedule_tag_pending=true WHERE id=%s', (candidate['id'],))
            conn.execute("DELETE FROM telegram_binding_jobs WHERE phase='done' AND (proxy_node_id=%s OR campaign_id=%s)", (node['id'], candidate['id']))
            job = conn.execute('''INSERT INTO telegram_binding_jobs(proxy_node_id,campaign_id,channel_id,channel_url,node_host,node_port)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING *,true AS due''',
                (node['id'],candidate['id'],candidate['channel_id'],candidate['telegram_url'],node['host'],node['port'])).fetchone()
        return {'state':'job','job_id':str(job['id']),'phase':job['phase'],'channel_url':job['channel_url'],
                'hosts':list(dict.fromkeys([node['host'],request['host']] + ([request['resolved_host']] if request.get('resolved_host') else []))),
                'port':node['port'],'expected_tag':job['ad_tag'],'telegram_channel_id':job['telegram_channel_id'],
                'owner_telegram_id':job['owner_telegram_id']}
    if not job or str(job['id']) != request.get('job_id') or job['phase'] == 'done':
        return {'accepted': False}
    if action == 'owner_error':
        code = request.get('code')
        code = code if code in ERRORS else 'telegram_unavailable'
        delay = request.get('retry_seconds', 300)
        delay = max(300, delay) if type(delay) is int and delay >= 0 else 300
        conn.execute("UPDATE telegram_binding_jobs SET error_code=%s,retry_at=now()+(%s*interval '1 second'),updated_at=now() WHERE id=%s", (code,delay,job['id']))
        return {'accepted': True}
    if not eligible(conn, job, node) or busy(conn, node['id']):
        return {'accepted': False}
    tag = request.get('tag', '')
    if not isinstance(tag, str) or not re.fullmatch('[0-9a-f]{32}', tag) or tag == '0'*32:
        return {'accepted': False}
    if conn.execute('''SELECT 1 FROM channel_promotion_bindings WHERE ad_tag=%s AND proxy_node_id<>%s
        UNION ALL SELECT 1 FROM proxy_sponsor_registrations WHERE ad_tag=%s AND proxy_node_id<>%s
        UNION ALL SELECT 1 FROM telegram_binding_jobs WHERE ad_tag=%s AND proxy_node_id<>%s''',
        (tag,node['id'],tag,node['id'],tag,node['id'])).fetchone():
        return {'accepted': False}
    if action == 'owner_authorize' and job['phase'] == 'inspect':
        if not all(type(request.get(k)) is int and request[k] > 0 for k in ('telegram_channel_id','owner_telegram_id')):
            return {'accepted': False}
        # Устаревшая связь удаляется ДО внешней записи. После сбоя её нельзя
        # повторно использовать как подтверждение для другого канала.
        conn.execute('DELETE FROM channel_promotion_bindings WHERE ad_tag=%s', (tag,))
        conn.execute("UPDATE telegram_binding_jobs SET ad_tag=%s,telegram_channel_id=%s,owner_telegram_id=%s,phase='bind',updated_at=now(),error_code=NULL WHERE id=%s",
                     (tag,request['telegram_channel_id'],request['owner_telegram_id'],job['id']))
        return {'accepted': True}
    if action != 'owner_verified' or job['phase'] not in ('bind','propagating') or job['ad_tag'] != tag:
        return {'accepted': False}
    if not all(type(request.get(k)) is int and request[k] > 0 for k in ('owner_telegram_id','telegram_channel_id','bot_message_id')):
        return {'accepted': False}
    wanted = job['channel_url'].rstrip('/').rsplit('/',1)[-1].lower()
    if request.get('channel_username') != wanted:
        return {'accepted': False}
    if job['owner_telegram_id'] and job['owner_telegram_id'] != request['owner_telegram_id']:
        return {'accepted': False}
    if job['telegram_channel_id'] != request['telegram_channel_id']:
        return {'accepted': False}
    if job['phase'] == 'bind':
        conn.execute('''UPDATE telegram_binding_jobs SET phase='propagating',owner_telegram_id=%s,
            telegram_channel_id=%s,bot_message_id=%s,verified_at=now(),retry_at=now()+interval '1 hour',
            error_code=NULL,updated_at=now() WHERE id=%s''',
            (request['owner_telegram_id'],request['telegram_channel_id'],request['bot_message_id'],job['id']))
        return {'accepted': True}
    if not job['due'] or job['telegram_channel_id'] != request['telegram_channel_id']:
        return {'accepted': False}
    conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
        VALUES(%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT(proxy_node_id,channel_id) DO UPDATE
        SET channel_url=EXCLUDED.channel_url,node_host=EXCLUDED.node_host,node_port=EXCLUDED.node_port,
            ad_tag=EXCLUDED.ad_tag,confirmed_by=NULL,confirmed_at=now()''',
        (node['id'],job['channel_id'],job['channel_url'],node['host'],node['port'],tag))
    conn.execute("UPDATE telegram_binding_jobs SET phase='done',error_code=NULL,updated_at=now() WHERE id=%s", (job['id'],))
    conn.execute('UPDATE campaigns SET auto_schedule_tag_pending=true WHERE id=%s', (job['campaign_id'],))
    conn.execute('''INSERT INTO telegram_binding_events(job_id,proxy_node_id,campaign_id,channel_id,
        owner_telegram_id,telegram_channel_id,bot_message_id) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (job['id'],node['id'],job['campaign_id'],job['channel_id'],request['owner_telegram_id'],
         request['telegram_channel_id'],request['bot_message_id']))
    return {'accepted': True}
