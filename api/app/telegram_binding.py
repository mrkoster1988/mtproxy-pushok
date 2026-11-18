"""Host-only очередь внешней привязки. Не HTTP API и не подтверждение показов."""
import json
import re
from datetime import datetime, timedelta, timezone
from .auto_schedule import earliest_gap, node_intervals

ERRORS = {
    'automation_disabled',
    'not_connected', 'revoked', 'connection_busy', 'account_mismatch', 'unexpected_mtproxy_bot',
    'public_channel_required', 'proxy_identity_not_verified', 'tag_not_verified',
    'proxy_not_found', 'proxy_ambiguous', 'proxy_list_loop', 'proxy_list_ambiguous',
    'proxy_list_too_long', 'ambiguous_bot_response', 'bot_response_timeout', 'tag_changed',
    'bot_button_expired', 'proxy_list_changed',
    'set_promotion_button_missing', 'channel_prompt_not_verified', 'binding_not_verified',
    'telegram_unavailable', 'telegram_flood_wait', 'owner_helper_unavailable', 'target_changed',
    'tag_used_by_other_node', 'node_busy',
    'channel_link_invalid', 'channel_not_found', 'channel_access_denied',
    'channel_not_broadcast', 'channel_username_mismatch',
}

CHANNEL_ERRORS = {
    'bot_button_expired': ('Кнопка @MTProxyBot уже изменилась', 'Агент заново откроет список прокси при следующей попытке. Повторный вход в Telegram не требуется.'),
    'proxy_list_changed': ('Список прокси в Telegram изменился', 'Агент повторно получит список и проверит адрес, порт и тег перед привязкой канала.'),
    'proxy_not_found': ('Прокси не найден в аккаунте владельца', 'Откройте @MTProxyBot → /myproxies в подключённом аккаунте и сверьте адрес и порт узла. Если тег уже связан с нужным каналом, сохраните его в «Ручной привязке готового тега».'),
    'public_channel_required': ('Канал не прошёл проверку', 'Обновлённый агент уточнит причину при следующей попытке. Проверьте ссылку на канал в заявке.'),
    'channel_link_invalid': ('Неверная ссылка на канал', 'Нужна ссылка вида https://t.me/username или https://telegram.me/username. Ссылка на отдельный пост или приглашение не подходит.'),
    'channel_not_found': ('Telegram не нашёл этот канал', 'Проверьте точный @username: канал мог сменить адрес или стать закрытым.'),
    'channel_access_denied': ('Аккаунту владельца недоступен канал', 'Проверьте доступ к каналу из подключённого аккаунта Telegram.'),
    'channel_not_broadcast': ('По ссылке находится не канал', 'Telegram вернул группу или личный аккаунт. Для спонсорства нужна ссылка на публичный канал.'),
    'channel_username_mismatch': ('Адрес канала не подтверждён', 'Указанный @username отсутствует среди активных имён канала. Проверьте его текущую публичную ссылку.'),
}


def channel_feedback(code):
    title, action = CHANNEL_ERRORS[code]
    return {'title': title, 'next_action': action}


def verified_mismatch(request):
    """Host worker подтвердил два разных публичных канала по числовым ID."""
    return (request.get('promotion_mismatch') is True and request.get('channel_username') is None
            and all(type(request.get(k)) is int and request[k] > 0
                    for k in ('telegram_channel_id', 'observed_channel_id'))
            and request['telegram_channel_id'] != request['observed_channel_id'])


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
            AND ch.id=%s AND ch.telegram_url=%s AND ch.archived_at IS NULL FOR SHARE OF s,ch''',
            (job['channel_id'],job['channel_url'])).fetchone())
    return bool(conn.execute('''SELECT 1 FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id
        WHERE c.id=%s AND c.channel_id=%s AND ch.telegram_url=%s
          AND c.archived_at IS NULL AND ch.archived_at IS NULL
          AND c.moderation_status='approved' AND c.payment_status='verified'
          AND NOT c.auto_schedule_paused AND c.placement_status IN ('not_scheduled','scheduled')
          AND (c.placement_status='not_scheduled' OR c.auto_schedule_managed)
          AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
          AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied') FOR UPDATE OF c,ch''',
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
    rows = conn.execute('''SELECT j.proxy_node_id,n.name,j.campaign_id,ch.title AS channel_title,j.channel_url,j.phase,j.retry_at,
        j.verified_at,j.error_code,j.updated_at,c.scheduled_start_at AS planned_start_at FROM telegram_binding_jobs j
        JOIN proxy_nodes n ON n.id=j.proxy_node_id LEFT JOIN campaigns c ON c.id=j.campaign_id
        JOIN sponsor_channels ch ON ch.id=j.channel_id WHERE n.archived_at IS NULL ORDER BY n.name''').fetchall()
    return [dict(row, **channel_feedback(row['error_code'])) if row['error_code'] in CHANNEL_ERRORS else row for row in rows]


def preparation_improves_slot(conn, campaign, node_id):
    """Не готовим новый тег, если существующая связь даёт более ранний полный слот."""
    now = datetime.now(timezone.utc)
    start = max(now, campaign['requested_start_at'] or now)
    duration = timedelta(seconds=campaign['duration_seconds_snapshot'])
    def available(node, after):
        return earliest_gap(after, duration, node_intervals(conn, node, campaign['id']))
    after_preparation = available(node_id,max(start,now+timedelta(hours=1,minutes=2)))
    prepared = conn.execute('''SELECT n.id FROM channel_promotion_bindings b JOIN proxy_nodes n ON n.id=b.proxy_node_id
        WHERE b.channel_id=%s AND b.channel_url=%s AND (b.node_host,b.node_port)=(n.host,n.port)
          AND n.status='active' AND n.archived_at IS NULL AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')
          AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.proxy_node_id=n.id AND j.phase<>'done')''',
        (campaign['channel_id'],campaign['telegram_url'])).fetchall()
    return not any(available(n['id'],start) <= after_preparation for n in prepared)


def release_waiting_forecasts(conn, node):
    """Убираем взаимную блокировку прогнозов, если текущий канал ещё не подготовлен.

    Все записи заблокированы общей блокировкой планировщика и FOR UPDATE.
    Ручное, наблюдавшееся или применённое размещение и действующие брони
    не изменяем. Следующий полный период назначается после готовности тега.
    """
    rows = conn.execute('''SELECT c.*,ch.telegram_url,ch.archived_at AS channel_archived,
        EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id) AS observed,
        EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.campaign_id=c.id AND j.phase<>'done') AS preparing,
        EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied') AS applied
        FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id
        WHERE c.proxy_node_id=%s AND c.placement_status IN ('scheduled','active')
        ORDER BY c.scheduled_start_at,c.id FOR UPDATE OF c''', (node['id'],)).fetchall()
    if not rows or any(c['placement_status'] != 'scheduled' or not c['auto_schedule_managed']
                       or c['auto_schedule_paused'] or c['archived_at'] or c['channel_archived']
                       or c['observed'] or c['applied'] or c['preparing']
                       or c['moderation_status'] != 'approved' or c['payment_status'] != 'verified' for c in rows):
        return False
    if conn.execute('SELECT 1 FROM slot_holds WHERE proxy_node_id=%s AND expires_at>now() AND ends_at>now()', (node['id'],)).fetchone():
        return False
    now = datetime.now(timezone.utc)
    head = next((c for c in rows if c['scheduled_end_at'] > now), None)
    if not head or head['scheduled_start_at'] > now + timedelta(hours=1) or (head['requested_start_at'] and head['requested_start_at'] > now + timedelta(hours=1)):
        return False
    if conn.execute('''SELECT 1 FROM channel_promotion_bindings b JOIN sponsor_channels ch ON ch.id=b.channel_id
        WHERE b.proxy_node_id=%s AND b.channel_id=%s AND b.channel_url=ch.telegram_url
          AND (b.node_host,b.node_port)=(%s,%s)''', (node['id'],head['channel_id'],node['host'],node['port'])).fetchone():
        return False
    if not preparation_improves_slot(conn, head, node['id']):
        return False
    for campaign in rows:
        conn.execute('''UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
            scheduled_start_at=NULL,scheduled_end_at=NULL,auto_schedule_tag_pending=true,updated_at=now()
            WHERE id=%s''', (campaign['id'],))
        conn.execute("INSERT INTO audit_events(campaign_id,event_type,details_json) VALUES(%s,'placement_auto_scheduled',%s::jsonb)",
            (campaign['id'],json.dumps(dict(reason='awaiting_telegram_preparation',previous_node_id=str(node['id']),
                previous_start_at=campaign['scheduled_start_at'].isoformat(),previous_end_at=campaign['scheduled_end_at'].isoformat()))))
    return head


def cached_target(conn, node, channel_id=None):
    """Перепроверяем ближайший автоматический слот либо спонсора свободного узла."""
    return conn.execute("""SELECT b.* FROM channel_promotion_bindings b
        JOIN sponsor_channels ch ON ch.id=b.channel_id
        WHERE b.proxy_node_id=%s AND (b.node_host,b.node_port)=(%s,%s) AND (%s::uuid IS NULL OR b.channel_id=%s)
          AND b.channel_url=ch.telegram_url AND ch.archived_at IS NULL
          AND NOT EXISTS(SELECT 1 FROM campaigns WHERE proxy_node_id=b.proxy_node_id AND
            (placement_status='active' OR (placement_status='scheduled' AND NOT auto_schedule_managed)))
          AND (EXISTS(SELECT 1 FROM campaigns c WHERE c.proxy_node_id=b.proxy_node_id
            AND c.channel_id=b.channel_id AND c.placement_status='scheduled' AND c.auto_schedule_managed
            AND c.archived_at IS NULL AND c.moderation_status='approved' AND c.payment_status='verified'
            AND NOT c.auto_schedule_paused AND c.scheduled_end_at>now()
            AND c.scheduled_start_at<=now()+interval '65 minutes')
          OR EXISTS(SELECT 1 FROM default_sponsor_settings WHERE singleton AND channel_id=b.channel_id))
        ORDER BY COALESCE(b.last_probe_at,b.confirmed_at),b.channel_id LIMIT 1 FOR UPDATE OF b""",
        (node['id'], node['host'], node['port'], channel_id, channel_id)).fetchone()


def handle(conn, request):
    # Та же блокировка, что у планировщика; короткие транзакции, без Telegram внутри.
    if not conn.execute('SELECT pg_try_advisory_xact_lock(736102482) AS ok').fetchone()['ok']:
        return {'state': 'wait'}
    node = resolve(conn, request)
    if not node:
        return {'state': 'wait'}
    job = conn.execute("""SELECT *,retry_at<=now() AS due,verified_at+interval '1 hour'<=now() AS matured
                       FROM telegram_binding_jobs WHERE proxy_node_id=%s FOR UPDATE""",
                       (node['id'],)).fetchone()
    action = request['action']
    if (action in ('owner_claim','owner_empty_cached','owner_mismatch_cached') and request.get('probe_cached') is True
            and (not job or job['phase'] == 'done') and node['status'] == 'active'
            and not node['archived_at'] and node['target_engine'] in (None, 'official_mtproxy')):
        cached = cached_target(conn, node, request.get('channel_id'))
        if action in ('owner_empty_cached','owner_mismatch_cached'):
            if action == 'owner_mismatch_cached' and not verified_mismatch(request):
                return {'accepted': False}
            # Запоздавший ответ не отменяет более новое ручное или автоматическое подтверждение.
            if cached and cached['last_probe_at'] and cached['last_probe_at'].isoformat() == request.get('probe_at') and (
                    cached['confirmed_at'] < cached['last_probe_at'] and cached['ad_tag'] == request.get('tag')
                    and str(cached['channel_id']) == request.get('channel_id')):
                conn.execute('DELETE FROM channel_promotion_bindings WHERE proxy_node_id=%s AND channel_id=%s',
                             (node['id'], cached['channel_id']))
                return {'accepted': True}
            return {'accepted': False}
        if cached and max(cached['last_probe_at'] or cached['confirmed_at'], cached['confirmed_at']) < datetime.now(timezone.utc)-timedelta(minutes=15):
            stamp = conn.execute("""UPDATE channel_promotion_bindings SET last_probe_at=now()
                WHERE proxy_node_id=%s AND channel_id=%s RETURNING last_probe_at""",
                (node['id'], cached['channel_id'])).fetchone()['last_probe_at']
            return {'state':'probe','probe_at':stamp.isoformat(),'expected_tag':cached['ad_tag'],
                    'channel_id':str(cached['channel_id']),'channel_url':cached['channel_url'],
                    'hosts':list(dict.fromkeys(filter(None, (node['host'],request['host'],request.get('resolved_host'))))),
                    'port':node['port']}
    if action == 'owner_claim':
        if job and job['phase'] != 'done':
            if not eligible(conn, job, node):
                # Отмена/изменение не даёт старому ответу создать новую связь.
                conn.execute("UPDATE telegram_binding_jobs SET phase='done',error_code='target_changed',updated_at=now() WHERE id=%s", (job['id'],))
                return {'state': 'wait'}
            if not job['due'] or busy(conn, node['id']):
                return {'state': 'wait'}
        else:
            if node['status'] != 'active' or node['archived_at'] or node['target_engine'] not in (None, 'official_mtproxy'):
                return {'state': 'wait'}
            resumed = release_waiting_forecasts(conn, node)
            if busy(conn, node['id']):
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
            candidate = resumed or next((c for c in candidates if preparation_improves_slot(conn,c,node['id'])),None)
            if not candidate:
                # Ручная связь уже готова для ожидающей кампании на этом узле.
                # Дадим планировщику назначить слот, не захватывая узел новой
                # проверкой канала по умолчанию сразу после ручного сохранения.
                if candidates and conn.execute('''SELECT 1 FROM channel_promotion_bindings b
                    JOIN sponsor_channels ch ON ch.id=b.channel_id
                    WHERE b.proxy_node_id=%s AND b.channel_id=ANY(%s)
                      AND b.channel_url=ch.telegram_url AND (b.node_host,b.node_port)=(%s,%s)
                    LIMIT 1''', (node['id'], [c['channel_id'] for c in candidates], node['host'], node['port'])).fetchone():
                    return {'state': 'wait'}
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
    if job['owner_telegram_id'] and job['owner_telegram_id'] != request['owner_telegram_id']:
        return {'accepted': False}
    if job['telegram_channel_id'] != request['telegram_channel_id']:
        return {'accepted': False}
    if request.get('channel_username') != wanted:
        if (job['phase'] == 'propagating' and job['due'] and job['matured']
                and request.get('channel_username') is None
                and (request.get('promotion_empty') is True or verified_mismatch(request))):
            # После паузы тот же узел явно показывает n/a или другой проверенный
            # канал. Повторяем привязку; неопределённый ответ её не сбрасывает.
            conn.execute("""UPDATE telegram_binding_jobs SET phase='bind',verified_at=NULL,
                bot_message_id=NULL,retry_at=now(),error_code=NULL,updated_at=now() WHERE id=%s""", (job['id'],))
            conn.execute("""UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
                scheduled_start_at=NULL,scheduled_end_at=NULL,auto_schedule_tag_pending=true
                WHERE id=%s AND auto_schedule_managed""", (job['campaign_id'],))
            return {'accepted': True}
        return {'accepted': False}
    if job['phase'] == 'bind':
        window = conn.execute('''UPDATE telegram_binding_jobs SET phase='propagating',owner_telegram_id=%s,
            telegram_channel_id=%s,bot_message_id=%s,verified_at=now(),retry_at=now()+interval '1 hour',
            error_code=NULL,updated_at=now() WHERE id=%s RETURNING retry_at''',
            (request['owner_telegram_id'],request['telegram_channel_id'],request['bot_message_id'],job['id'])).fetchone()
        if job['campaign_id']:
            # Пока Telegram готовил голову очереди, могли появиться прогнозы
            # следующих каналов. Они сдвигаются вслед за ней, а не вытесняют её.
            # Ручные, наблюдавшиеся и уже применённые периоды остаются занятыми.
            tail = conn.execute('''SELECT c.* FROM campaigns c WHERE c.proxy_node_id=%s AND c.id<>%s
                AND c.placement_status='scheduled' AND c.auto_schedule_managed AND c.auto_schedule_tag_pending
                AND NOT c.auto_schedule_paused AND c.archived_at IS NULL
                AND c.moderation_status='approved' AND c.payment_status='verified'
                AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.campaign_id=c.id AND j.phase<>'done')
                AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
                AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')
                FOR UPDATE OF c''', (node['id'],job['campaign_id'])).fetchall()
            for following in tail:
                conn.execute('''UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
                    scheduled_start_at=NULL,scheduled_end_at=NULL,updated_at=now() WHERE id=%s''', (following['id'],))
                conn.execute("INSERT INTO audit_events(campaign_id,event_type,details_json) VALUES(%s,'placement_auto_scheduled',%s::jsonb)",
                    (following['id'],json.dumps(dict(reason='preceding_preparation_retimed',
                        previous_node_id=str(node['id']),previous_start_at=following['scheduled_start_at'].isoformat(),
                        previous_end_at=following['scheduled_end_at'].isoformat()))))
            campaign = conn.execute('SELECT * FROM campaigns WHERE id=%s FOR UPDATE', (job['campaign_id'],)).fetchone()
            floor = window['retry_at']
            duration = timedelta(seconds=campaign['duration_seconds_snapshot'])
            earliest = max(floor, campaign['requested_start_at'] or floor)
            intervals = node_intervals(conn, node['id'], campaign['id'])
            hold = conn.execute('''SELECT starts_at,ends_at FROM slot_holds WHERE campaign_id=%s
                AND proxy_node_id=%s AND expires_at>now()''', (campaign['id'], node['id'])).fetchone()
            start = earliest_gap(earliest, duration, intervals)
            if (hold and hold['starts_at'] >= earliest and hold['ends_at']-hold['starts_at'] == duration
                    and earliest_gap(hold['starts_at'], duration, intervals) == hold['starts_at']):
                start = hold['starts_at']
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s', (campaign['id'],))
            conn.execute('''UPDATE campaigns SET auto_start_not_before=%s,proxy_node_id=%s,
                scheduled_start_at=%s,scheduled_end_at=%s,placement_status='scheduled',
                auto_schedule_managed=true,auto_schedule_tag_pending=true,updated_at=now() WHERE id=%s''',
                (floor, node['id'], start, start+duration, campaign['id']))
        return {'accepted': True}
    if not job['due'] or not job['matured']:
        return {'accepted': False}
    conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
        VALUES(%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT(proxy_node_id,channel_id) DO UPDATE
        SET channel_url=EXCLUDED.channel_url,node_host=EXCLUDED.node_host,node_port=EXCLUDED.node_port,
            ad_tag=EXCLUDED.ad_tag,confirmed_by=NULL,confirmed_at=now()''',
        (node['id'],job['channel_id'],job['channel_url'],node['host'],node['port'],tag))
    conn.execute("UPDATE telegram_binding_jobs SET phase='done',error_code=NULL,updated_at=now() WHERE id=%s", (job['id'],))
    conn.execute('''UPDATE campaigns SET auto_schedule_tag_pending=true,
        auto_start_not_before=%s WHERE id=%s''', (job['verified_at'] + timedelta(hours=1), job['campaign_id']))
    conn.execute('''INSERT INTO telegram_binding_events(job_id,proxy_node_id,campaign_id,channel_id,
        owner_telegram_id,telegram_channel_id,bot_message_id) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (job['id'],node['id'],job['campaign_id'],job['channel_id'],request['owner_telegram_id'],
         request['telegram_channel_id'],request['bot_message_id']))
    return {'accepted': True}
