"""Автоподбор полного периода после оплаты. Выбор подтверждённого тега без изменения Telegram."""
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


def node_intervals(conn, node_id, campaign_id, *, prepare_channel_id=None):
    rows = conn.execute("""SELECT scheduled_start_at AS left_at,scheduled_end_at AS right_at,channel_id
        FROM campaigns WHERE proxy_node_id=%s AND id<>%s AND placement_status IN ('scheduled','active')
        UNION ALL SELECT h.starts_at,h.ends_at,c.channel_id FROM slot_holds h
        JOIN campaigns c ON c.id=h.campaign_id
        WHERE h.proxy_node_id=%s AND h.campaign_id<>%s AND h.expires_at>now()""",
        (node_id, campaign_id, node_id, campaign_id)).fetchall()
    # Для нового канала резервируем час после предыдущего показа и перед
    # следующим другим каналом. Уже подготовленный отдельный тег этого не требует.
    # Это прогноз: готовность по-прежнему подтверждает внешний агент.
    return [(row['left_at'] - timedelta(hours=1), row['right_at'] + timedelta(hours=1))
            if prepare_channel_id is not None and row['channel_id'] != prepare_channel_id
            else (row['left_at'], row['right_at']) for row in rows]


def service_once(dsn, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError('limit_out_of_range')
    scheduled = 0
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row,
                         options='-c statement_timeout=5000') as conn:
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
            AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.campaign_id=campaigns.id AND j.phase<>'done')
            AND EXISTS(SELECT 1 FROM sponsor_channels ch WHERE ch.id=campaigns.channel_id AND ch.archived_at IS NULL)
            ORDER BY COALESCE(requested_start_at,created_at),created_at,id LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,)).fetchall()
        # Внешняя привязка блокирует изменение спонсора, но не расчёт очереди.
        # После подтверждения Telegram текущий полный период уже известен:
        # его можно учитывать как занятый интервал для следующих прогнозов.
        # inspect/bind и неопределённый результат без периода остаются закрыты.
        nodes = conn.execute("""SELECT id FROM proxy_nodes WHERE status='active' AND archived_at IS NULL
            AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.proxy_node_id=proxy_nodes.id AND j.phase<>'done'
                AND NOT EXISTS(SELECT 1 FROM campaigns c WHERE c.id=j.campaign_id
                    AND j.phase='propagating' AND j.verified_at IS NOT NULL AND j.error_code IS NULL
                    AND (j.node_host,j.node_port)=(proxy_nodes.host,proxy_nodes.port)
                    AND c.proxy_node_id=proxy_nodes.id AND c.placement_status='scheduled'
                    AND c.auto_schedule_managed AND NOT c.auto_schedule_paused
                    AND c.archived_at IS NULL AND c.moderation_status='approved' AND c.payment_status='verified'
                    AND c.scheduled_end_at>now()))
            AND (target_engine IS NULL OR target_engine='official_mtproxy') ORDER BY id FOR UPDATE""").fetchall()
        # Ранее выданный прогноз мог попасть на узел без тега. Как только
        # появилась подходящая подтверждённая связь, переоцениваем весь период.
        # Ручные переносы, наблюдения и применённые агентом кампании не трогаем.
        waiting = conn.execute("""SELECT c.* FROM campaigns c
            WHERE c.archived_at IS NULL AND c.moderation_status='approved'
              AND c.payment_status='verified' AND c.placement_status='scheduled'
              AND NOT c.auto_schedule_paused
              AND c.auto_schedule_managed
              AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.campaign_id=c.id AND j.phase<>'done')
              AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
              AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')
              AND EXISTS(SELECT 1 FROM sponsor_channels ch WHERE ch.id=c.channel_id AND ch.archived_at IS NULL)
              AND (c.auto_schedule_tag_pending OR NOT EXISTS(
                SELECT 1 FROM channel_promotion_bindings b
                JOIN proxy_nodes n ON n.id=b.proxy_node_id JOIN sponsor_channels ch ON ch.id=b.channel_id
                WHERE b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
                  AND ch.archived_at IS NULL AND n.status='active' AND n.archived_at IS NULL
                  AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')
                  AND b.channel_url=ch.telegram_url AND (b.node_host,b.node_port)=(n.host,n.port)))
            ORDER BY COALESCE(c.requested_start_at,c.created_at),c.created_at,c.id
            FOR UPDATE OF c""").fetchall()
        reassigning = {c['id']:c for c in waiting}
        # Пересчитываем неподготовленный хвост вместе: старые прогнозы не должны
        # блокировать друг друга после переноса начала текущей кампании. Внешние
        # задания, ручные слоты и фактические показы в waiting не попадают.
        if waiting:
            conn.execute("""UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
                scheduled_start_at=NULL,scheduled_end_at=NULL,auto_schedule_tag_pending=true
                WHERE id=ANY(%s)""", (list(reassigning),))
        now = datetime.now(timezone.utc)
        for c in sorted(campaigns+waiting,key=lambda row:(row['requested_start_at'] or row['created_at'],row['created_at'],str(row['id']))):
            floor = c['auto_start_not_before'] or now + timedelta(hours=1)
            start = max(c['requested_start_at'] or now, now, floor)
            duration = timedelta(seconds=c['duration_seconds_snapshot'])
            prepared = conn.execute("""SELECT b.proxy_node_id,
                CASE WHEN b.confirmed_by IS NOT NULL THEN b.confirmed_at+interval '1 hour' END AS ready_at FROM channel_promotion_bindings b
                JOIN proxy_nodes n ON n.id=b.proxy_node_id
                JOIN sponsor_channels ch ON ch.id=b.channel_id
                WHERE b.channel_id=%s AND n.status='active' AND n.archived_at IS NULL
                AND ch.archived_at IS NULL AND b.channel_url=ch.telegram_url
                AND b.node_host=n.host AND b.node_port=n.port
                AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.proxy_node_id=n.id AND j.phase<>'done')
                AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')
                FOR SHARE OF b""",(c['channel_id'],)).fetchall()
            prepared_ids = {str(p['proxy_node_id']): p['ready_at'] for p in prepared}
            # Связь тега определяет пригодные узлы для запуска, но её отсутствие
            # не должно оставлять оплаченную заявку без расписания.
            hold = conn.execute("""SELECT proxy_node_id,starts_at,ends_at FROM slot_holds
                WHERE campaign_id=%s AND expires_at>now()""", (c['id'],)).fetchone()
            candidates = []
            for node in nodes:
                if prepared_ids and str(node["id"]) not in prepared_ids:
                    continue
                needs_preparation = str(node['id']) not in prepared_ids
                node_start = max(start, prepared_ids.get(str(node['id'])) or start)
                intervals = node_intervals(conn, node['id'], c['id'],
                                          prepare_channel_id=c['channel_id'] if needs_preparation else None)
                available = earliest_gap(node_start, duration, intervals)
                # Уважить ещё действующую бронь, созданную до обновления кабинета.
                held = bool(hold and hold['proxy_node_id']==node['id'] and hold['starts_at']>=node_start
                            and hold['ends_at']-hold['starts_at']==duration
                            and earliest_gap(hold['starts_at'],duration,intervals)==hold['starts_at'])
                if held:
                    available = hold['starts_at']
                # При одинаковом времени сохраняем выбранный узел. Появление
                # ещё одного узла без готового тега не должно тасовать очередь.
                candidates.append((0 if held else 1,available,
                                   0 if node['id'] == c['proxy_node_id'] else 1,str(node['id'])))
            if not candidates:
                continue
            _,at,_,node_id = min(candidates)
            previous = reassigning.get(c['id'])
            unchanged = bool(previous and str(previous['proxy_node_id']) == node_id
                             and previous['scheduled_start_at'] == at
                             and previous['scheduled_end_at'] == at+duration
                             and previous['auto_schedule_tag_pending'] == (node_id not in prepared_ids)
                             and previous['auto_start_not_before'] == floor)
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s',(c['id'],))
            conn.execute("""UPDATE campaigns SET proxy_node_id=%s,scheduled_start_at=%s,
                scheduled_end_at=%s,placement_status='scheduled',auto_schedule_managed=true,
                auto_schedule_tag_pending=%s,auto_start_not_before=%s,
                updated_at=CASE WHEN %s THEN updated_at ELSE now() END WHERE id=%s""",
                (node_id,at,at+duration,node_id not in prepared_ids,floor,unchanged,c['id']))
            if unchanged:
                continue
            details = {'node_id':node_id,'start_at':at.isoformat(),'end_at':(at+duration).isoformat(),
                       'requested_start_at':c['requested_start_at'].isoformat() if c['requested_start_at'] else None,
                       'shifted':at>start,'promotion_prepared':node_id in prepared_ids}
            if c['id'] in reassigning:
                details.update(reason='prepared_tag_selected' if node_id in prepared_ids else 'forecast_recalculated',previous_node_id=str(previous['proxy_node_id']),
                               previous_start_at=previous['scheduled_start_at'].isoformat(),
                               previous_end_at=previous['scheduled_end_at'].isoformat())
            if c['id'] in recovered:
                previous = recovered[c['id']]
                details.update(reason='missed_start_recovered',previous_start_at=previous['scheduled_start_at'].isoformat(),
                               previous_end_at=previous['scheduled_end_at'].isoformat())
            conn.execute("INSERT INTO audit_events(campaign_id,event_type,details_json) VALUES(%s,'placement_auto_scheduled',%s::jsonb)",
                         (c['id'],json.dumps(details)))
            scheduled += 1
    return {'scheduled': scheduled}
