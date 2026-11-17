"""Ограниченное расширение сети по очереди или явной настройке первого резерва."""
import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.request import Request
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auto_schedule import earliest_gap
from . import channel_ai_connection

LOCK = 736102482  # Общий с назначением слотов и привязкой Telegram.
ERRORS = {'resources_low', 'port_busy', 'owner_unavailable', 'registration_uncertain',
          'registration_prompt_invalid', 'proxy_not_found', 'proxy_ambiguous',
          'proxy_identity_not_verified', 'tag_not_verified', 'tag_changed',
          'telegram_flood_wait', 'telegram_unavailable', 'bot_response_timeout',
          'ambiguous_bot_response', 'start_failed', 'host_unavailable',
          'policy_changed', 'registration_conflict', 'agent_setup_failed',
          'registration_secret_mismatch', 'connection_busy', 'owner_helper_unavailable',
          'preparation_interrupted', 'recovery_pending', 'native_unavailable', 'timer_unavailable'}

READINESS = {
    'host': {'ready', 'host_unavailable'},
    'native': {'ready', 'native_unavailable'},
    'timers': {'ready', 'timer_unavailable'},
    'telegram': {'ready', 'owner_unavailable', 'connection_busy', 'telegram_flood_wait'},
    'resources': {'ready', 'resources_low'},
    'api': {'ready', 'api_unavailable', 'api_timeout', 'api_invalid_response', 'agent_busy'},
    'policy': {'ready', 'disabled', 'ai_unavailable', 'owner_unavailable', 'unknown'},
    'ports': {'ready', 'port_busy', 'port_check_failed', 'preparation_pending', 'instance_limit', 'unknown'},
    'capacity': {'ready', 'instance_limit', 'preparation_pending', 'preparation_failed', 'daily_limit', 'unknown'},
}


def queue_report(raw):
    """В публичном отчёте только счётчики, без заявок, каналов и секретов."""
    try:
        fields = ('node_count', 'campaign_count', 'delayed_count', 'max_delay_seconds', 'wait_minutes')
        result = {key: raw[key] for key in fields}
        if any(type(value) is not int or value < 0 for value in result.values()):
            return None
        if (result['node_count'] > 1000000 or not 0 <= result['delayed_count'] <= result['campaign_count'] <= 100
                or result['max_delay_seconds'] > 315537897600 or not 90 <= result['wait_minutes'] <= 10080
                or type(raw['limited']) is not bool):
            return None
        if ((not result['node_count'] or not result['campaign_count']) and
                (result['delayed_count'] or result['max_delay_seconds'])):
            return None
        if bool(result['delayed_count']) != (result['max_delay_seconds'] >= result['wait_minutes']*60):
            return None
        result['limited'] = raw['limited']
        # Причина выводится из проверенных чисел, а не из произвольного текста агента.
        result['reason'] = ('no_nodes' if not result['node_count'] else
                            'no_campaigns' if not result['campaign_count'] else
                            'delayed' if result['delayed_count'] else 'below_threshold')
        return result
    except (KeyError, TypeError):
        return None


def readiness_report(policy_revision=None):
    from . import control
    raw = control.read('proxy-readiness.json', {})
    try:
        if raw.get('schema') != 1 or not isinstance(raw.get('checks'), dict):
            return None
        checks = {key: raw['checks'][key] for key in READINESS}
        if any(not isinstance(value, str) or value not in READINESS[key] for key, value in checks.items()):
            return None
        checked = datetime.fromisoformat(raw['checked_at'])
        age = (datetime.now(timezone.utc)-checked).total_seconds()
        if age < 0:
            return None
        result = dict(checked_at=checked.isoformat(), recent=age <= 660, checks=checks,
                      current_policy=policy_revision is not None and raw.get('policy_revision')==str(policy_revision))
        if raw.get('source') in ('manual', 'scheduled'):
            result['source'] = raw['source']
        result['queue'] = queue_report(raw.get('queue')) if checks['api'] == 'ready' else None
        retry = datetime.fromisoformat(raw.get('retry_at', '')) if raw.get('retry_at') else None
        if retry and checks['telegram']=='telegram_flood_wait' and 0 < (retry-datetime.now(timezone.utc)).total_seconds() <= 2147483648:
            result['retry_at'] = retry.isoformat()
        return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    reserve_enabled: bool = Field(default=False, strict=True)
    max_instances: int = Field(ge=1, le=3, strict=True)
    wait_minutes: int = Field(ge=90, le=10080, strict=True)
    first_port: int = Field(ge=1024, le=65533, strict=True)
    revision: UUID

    @model_validator(mode='after')
    def reserved_ports(self):
        if set(range(self.first_port,self.first_port+self.max_instances)) & {19999,20000,20001,20002}:
            raise ValueError('reserved_stats_port')
        return self


def configuration(conn):
    return conn.execute('SELECT * FROM proxy_provisioning_settings WHERE singleton FOR UPDATE').fetchone()


def public(conn):
    from . import control
    cfg = dict(configuration(conn))
    cfg.pop('updated_by')
    jobs = conn.execute('''SELECT j.id,j.state,j.reason,j.attempts,j.retry_at,j.error_code,j.created_at,
        n.name,n.host,n.port,n.status FROM proxy_provisioning_jobs j
        JOIN proxy_nodes n ON n.id=j.proxy_node_id ORDER BY j.created_at DESC LIMIT 20''').fetchall()
    agent = control.read('proxy-provisioner.json',{})
    if not isinstance(agent,dict) or agent.get('code') not in ERRORS | {'disabled','ready','waiting','wait','provisioning','active','agent_busy','no_demand','limit_reached','daily_limit','evaluation_cooldown','ai_unavailable','retry_later'}:
        agent = {}
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(agent['checked_at'])).total_seconds()
        retry_at = agent.get('retry_at')
        agent = {'code':agent['code'],'checked_at':agent['checked_at'],'recent':0<=age<=660,
                 'current_policy':agent.get('policy_revision')==str(cfg['revision'])}
        if isinstance(retry_at,str):
            retry = datetime.fromisoformat(retry_at)
            if retry.tzinfo and 0 < (retry-datetime.now(timezone.utc)).total_seconds() <= 2147483648:
                agent['retry_at'] = retry.isoformat()
    except (KeyError,TypeError,ValueError):
        agent = None
    return dict(cfg, jobs=jobs, agent=agent, readiness=readiness_report(cfg['revision']),
                ai_ready=bool(channel_ai_connection.active('proxy_provisioning')))


def router_for(dsn, require_owner):
    router = APIRouter()

    @router.get('/api/v1/admin/proxy-provisioning')
    def read(owner=Depends(require_owner)):
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:
            return public(conn)

    @router.put('/api/v1/admin/proxy-provisioning')
    def save(data: Settings, owner=Depends(require_owner)):
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            cfg = configuration(conn)
            if cfg['revision'] != data.revision:
                raise HTTPException(409, 'provisioning_changed')
            if data.enabled and not channel_ai_connection.active('proxy_provisioning'):
                raise HTTPException(409, 'provisioning_ai_required')
            conn.execute('''UPDATE proxy_provisioning_settings SET enabled=%s,reserve_enabled=%s,max_instances=%s,
                wait_minutes=%s,first_port=%s,revision=%s,updated_by=%s,updated_at=now(),
                evaluated_at=NULL,decision=%s WHERE singleton''',
                (data.enabled,data.reserve_enabled,data.max_instances,data.wait_minutes,data.first_port,uuid4(),owner['id'],
                 'waiting' if data.enabled else 'disabled'))
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'proxy_provisioning_settings',%s::jsonb)",
                         (owner['id'],json.dumps(data.model_dump(mode='json'))))
            return public(conn)

    @router.post('/api/v1/admin/proxy-provisioning/{job_id}/retry')
    def retry(job_id: UUID, owner=Depends(require_owner)):
        with psycopg.connect(dsn(),row_factory=dict_row) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
            cfg = configuration(conn)
            if not allowed(conn,cfg):
                raise HTTPException(409,'provisioning_ai_required')
            current = conn.execute('SELECT error_code,retry_at FROM proxy_provisioning_jobs WHERE id=%s FOR UPDATE',(job_id,)).fetchone()
            if current and current['error_code']=='telegram_flood_wait' and current['retry_at']>datetime.now(timezone.utc):
                raise HTTPException(409,'provisioning_telegram_cooldown')
            if conn.execute("SELECT 1 FROM proxy_provisioning_jobs WHERE state='pending' AND id<>%s",(job_id,)).fetchone():
                raise HTTPException(409,'provisioning_busy')
            row = conn.execute('''UPDATE proxy_provisioning_jobs j SET state='pending',attempts=0,
                retry_at=now(),policy_revision=%s,owner_id=%s,error_code=NULL,updated_at=now()
                FROM proxy_nodes n WHERE j.id=%s AND n.id=j.proxy_node_id AND n.archived_at IS NULL
                AND j.state<>'active' RETURNING j.id''',(cfg['revision'],owner['id'],job_id)).fetchone()
            if not row:
                raise HTTPException(409,'provisioning_not_retryable')
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'proxy_provisioning_retry',jsonb_build_object('job_id',%s::text))",(owner['id'],str(job_id)))
            return public(conn)
    return router


def queue_summary(conn, host, minutes, aliases=()):
    """Та же очередь для диагностики и решения. Только задержка из-за слотов."""
    nodes = conn.execute('''SELECT id FROM proxy_nodes WHERE host=ANY(%s) AND status='active'
        AND archived_at IS NULL AND (target_engine IS NULL OR target_engine='official_mtproxy')''', ([host,*aliases],)).fetchall()
    now = datetime.now(timezone.utc)
    campaigns = conn.execute('''SELECT c.* FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id
        WHERE c.moderation_status='approved' AND c.payment_status='verified'
        AND NOT c.auto_schedule_paused AND c.archived_at IS NULL AND ch.archived_at IS NULL
        AND ch.telegram_channel_id IS NOT NULL
        AND (c.requested_start_at IS NULL OR c.requested_start_at <= %s)
        AND (c.placement_status='not_scheduled' OR (c.placement_status='scheduled' AND c.auto_schedule_managed))
        AND NOT EXISTS(SELECT 1 FROM placement_observations o WHERE o.campaign_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')
        AND NOT EXISTS(SELECT 1 FROM telegram_binding_jobs j WHERE j.channel_id=c.channel_id
            AND j.phase<>'done' AND j.error_code IS NOT NULL)
        ORDER BY c.created_at,c.id LIMIT 101''', (now + timedelta(hours=24),)).fetchall()
    result = dict(node_count=len(nodes), campaign_count=min(len(campaigns), 100),
                  delayed_count=0, max_delay_seconds=0, wait_minutes=minutes, limited=len(campaigns)>100)
    if not nodes:
        return queue_report(result)
    node_ids = [node['id'] for node in nodes]
    # Один снимок занятости вместо отдельного SQL-запроса для каждой пары узел/заявка.
    rows = conn.execute('''SELECT proxy_node_id,id AS campaign_id,scheduled_start_at AS left_at,
        scheduled_end_at AS right_at FROM campaigns
        WHERE proxy_node_id=ANY(%s) AND placement_status IN ('scheduled','active')
        UNION ALL SELECT proxy_node_id,campaign_id,starts_at,ends_at FROM slot_holds
        WHERE proxy_node_id=ANY(%s) AND expires_at>now()''', (node_ids, node_ids)).fetchall()
    occupied = {node_id: [] for node_id in node_ids}
    for row in rows:
        occupied[row['proxy_node_id']].append(row)
    for campaign in campaigns[:100]:
        start = max(now, campaign['requested_start_at'] or now)
        slots = []
        for node in nodes:
            slots.append(earliest_gap(start,timedelta(seconds=campaign['duration_seconds_snapshot']),
                [(r['left_at'],r['right_at']) for r in occupied[node['id']] if r['campaign_id'] != campaign['id']]))
        delay = min(slots) - start
        result['max_delay_seconds'] = max(result['max_delay_seconds'], int(delay.total_seconds()))
        if delay >= timedelta(minutes=minutes):
            result['delayed_count'] += 1
    return queue_report(result)


def demand(conn, host, minutes, aliases=()):
    return queue_summary(conn, host, minutes, aliases)['delayed_count']


def host_aliases(request):
    alias = request.get('primary_host')
    return [alias] if isinstance(alias, str) and re.fullmatch('[a-zA-Z0-9.-]{1,253}', alias) else []


def advise(metrics, config):
    from .channel_topic_suggestions import read_url, reserve_ai_call
    reserve_ai_call()
    schema = {'type':'object','properties':{'action':{'type':'string','enum':['create_proxy','wait']}},
              'required':['action'],'additionalProperties':False}
    body = {'model':config[1], 'store':False, 'max_completion_tokens':80,
        'messages':[{'role':'system','content':'Decide whether to create ONE additional MTProxy instance on an existing server. '
            'All metrics are server-verified. Create only for delayed eligible campaigns when capacity and resources permit. '
            'This adds a port, not a server or new audience. Do not create for errors. If uncertain, wait.'},
            {'role':'user','content':json.dumps(metrics)}],
        'response_format':{'type':'json_schema','json_schema':{'name':'proxy_capacity','strict':True,'schema':schema}}}
    raw = read_url(Request('https://api.openai.com/v1/chat/completions',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+config[0]},method='POST'),4,4096)
    choice = json.loads(raw)['choices'][0]
    if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
        raise ValueError('ai_response_invalid')
    result = json.loads(choice['message']['content'])
    if not isinstance(result,dict) or set(result) != {'action'} or result['action'] not in ('create_proxy','wait'):
        raise ValueError('ai_response_invalid')
    return result['action']


def allowed(conn, cfg):
    return bool(cfg['enabled'] and channel_ai_connection.active('proxy_provisioning') and cfg['updated_by']
        and conn.execute("SELECT 1 FROM users WHERE id=%s AND is_owner AND role='admin' AND blocked_at IS NULL",(cfg['updated_by'],)).fetchone())


def reserved_ports(conn, host, aliases=()):
    from .proxy_gateway import TOPICS
    ports = {r['port'] for r in conn.execute('SELECT port FROM proxy_nodes WHERE host=ANY(%s)', ([host,*aliases],))}
    ports.update((19999,20000,20001,20002))
    gateway = conn.execute('SELECT * FROM proxy_gateway_settings WHERE singleton').fetchone()
    if gateway['enabled']:
        ports.update(range(gateway['port'],gateway['port']+(len(TOPICS) if gateway['interests_enabled'] else 0)+1))
    return ports


def handle(conn, request):
    """Вызывается только через docker exec владельцем хоста; не HTTP."""
    if request['action'] == 'inspect':
        # Диагностика не берёт задания, не расходует попытки и не вызывает ИИ.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        host = str(ipaddress.IPv4Address(request['host']))
        if not ipaddress.ip_address(host).is_global:
            raise ValueError('invalid_host')
        cfg = conn.execute('SELECT * FROM proxy_provisioning_settings WHERE singleton').fetchone()
        ai_ready = bool(channel_ai_connection.active('proxy_provisioning'))
        owner_ready = bool(cfg['updated_by'] and conn.execute(
            "SELECT 1 FROM users WHERE id=%s AND is_owner AND role='admin' AND blocked_at IS NULL", (cfg['updated_by'],)).fetchone())
        jobs = conn.execute('SELECT state,created_at FROM proxy_provisioning_jobs').fetchall()
        capacity = ('preparation_pending' if any(j['state']=='pending' for j in jobs) else
                    'preparation_failed' if any(j['state']=='failed' for j in jobs) else
                    'instance_limit' if len(jobs)>=cfg['max_instances'] else
                    'daily_limit' if any(datetime.now(timezone.utc)-j['created_at']<timedelta(days=1) for j in jobs) else 'ready')
        return dict(first_port=cfg['first_port'], max_instances=cfg['max_instances'], capacity=capacity,
                    queue=queue_summary(conn, host, cfg['wait_minutes'], host_aliases(request)),
                    policy_revision=str(cfg['revision']),
                    reserved_ports=sorted(reserved_ports(conn,host,host_aliases(request))),
                    policy='disabled' if not cfg['enabled'] else 'ai_unavailable' if not ai_ready else
                           'owner_unavailable' if not owner_ready else 'ready')
    if not conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(LOCK,)).fetchone()['ok']:
        return {'state':'wait'}
    cfg = configuration(conn)
    host = str(ipaddress.IPv4Address(request['host']))
    if not ipaddress.ip_address(host).is_global:
        raise ValueError('invalid_host')
    # Восстановление работает и после отключения ИИ: рабочий узел не останавливаем,
    # незавершённый экземпляр можно убрать без новой регистрации Telegram.
    if request['action'] in ('recover','interrupted','complete'):
        identity_field = 'proxy_node_id' if request['action']=='recover' else 'id'
        requested = request.get('node_id' if identity_field=='proxy_node_id' else 'job_id')
        recovered = conn.execute(f'''SELECT j.*,n.host,n.port,n.status,n.archived_at FROM proxy_provisioning_jobs j
            JOIN proxy_nodes n ON n.id=j.proxy_node_id WHERE j.{identity_field}=%s FOR UPDATE OF j,n''',(requested,)).fetchone()
        if not recovered or recovered['host']!=host or (request['action']=='recover' and recovered['port']!=request.get('port')):
            return {'state':'unknown','accepted':False}
        if request['action'] in ('recover','interrupted') and recovered['state']!='active':
            in_use = recovered['status']=='active' or conn.execute('''SELECT 1 FROM campaigns
                WHERE proxy_node_id=%s AND placement_status='active' UNION ALL SELECT 1 FROM promotion_runtime
                WHERE proxy_node_id=%s AND state='applied' ''',(recovered['proxy_node_id'],recovered['proxy_node_id'])).fetchone()
            if in_use:
                return {'state':'unknown','accepted':False}
        permitted = allowed(conn,cfg) and recovered['policy_revision']==cfg['revision'] and not recovered['archived_at']
        if request['action']=='recover':
            return {'state':'active' if recovered['state']=='active' else 'pending' if recovered['state']=='pending' and permitted else 'blocked',
                    'job_id':str(recovered['id'])}
        if request['action']=='interrupted':
            if recovered['state']=='active':
                return {'accepted':False}  # Подтверждённый экземпляр здесь не изменяется.
            conn.execute('''UPDATE proxy_provisioning_jobs SET
                state=CASE WHEN NOT %s OR attempts>=5 OR state='failed' THEN 'failed' ELSE 'pending' END,
                error_code=COALESCE(error_code,%s),updated_at=now() WHERE id=%s''',
                (bool(permitted),'preparation_interrupted' if permitted else 'policy_changed',recovered['id']))
            return {'accepted':True}
        if recovered['state']=='active':
            registered = conn.execute('SELECT ad_tag FROM proxy_sponsor_registrations WHERE proxy_node_id=%s',(recovered['proxy_node_id'],)).fetchone()
            return {'accepted':bool(registered and request.get('ready') is True and registered['ad_tag']==request.get('tag'))}
    if request['action'] == 'settings':
        return dict(enabled=allowed(conn,cfg), first_port=cfg['first_port'], max_instances=cfg['max_instances'],
                    policy_revision=str(cfg['revision']))
    if not allowed(conn,cfg):
        return {'state':'disabled','accepted':False}
    job = conn.execute('''SELECT j.*,n.host,n.port,n.archived_at FROM proxy_provisioning_jobs j
        JOIN proxy_nodes n ON n.id=j.proxy_node_id WHERE j.state='pending' FOR UPDATE OF j,n''').fetchone()
    if request['action']=='authorize':
        return {'accepted':bool(job and str(job['id'])==request.get('job_id') and job['host']==host
            and job['policy_revision']==cfg['revision'] and not job['archived_at'])}
    if request['action'] in ('complete','failed'):
        if not job or str(job['id']) != request.get('job_id') or job['host'] != host:
            return {'accepted':False}
        if job['policy_revision'] != cfg['revision'] or job['archived_at']:
            return {'accepted':False}
        if request['action'] == 'failed':
            code = request.get('code') if request.get('code') in ERRORS else 'host_unavailable'
            delay = request.get('retry_seconds',900)
            ceiling = 2147483647 if code=='telegram_flood_wait' else 604800
            delay = max(900,min(ceiling,delay)) if type(delay) is int else 900
            conn.execute('''UPDATE proxy_provisioning_jobs SET error_code=%s,
                state=CASE WHEN attempts>=5 OR %s='registration_uncertain' THEN 'failed' ELSE 'pending' END,
                retry_at=now()+(%s*interval '1 second'),updated_at=now() WHERE id=%s''',(code,code,delay,job['id']))
            return {'accepted':True}
        tag = request.get('tag')
        if not isinstance(tag,str) or not re.fullmatch('[a-f0-9]{32}',tag) or tag == '0'*32 or request.get('ready') is not True:
            return {'accepted':False,'reason':'tag_not_verified'}
        if conn.execute('''SELECT 1 FROM proxy_sponsor_registrations WHERE ad_tag=%s AND proxy_node_id<>%s
            UNION ALL SELECT 1 FROM channel_promotion_bindings WHERE ad_tag=%s AND proxy_node_id<>%s
            UNION ALL SELECT 1 FROM telegram_binding_jobs WHERE ad_tag=%s AND proxy_node_id<>%s''',
            (tag,job['proxy_node_id'],tag,job['proxy_node_id'],tag,job['proxy_node_id'])).fetchone():
            return {'accepted':False,'reason':'registration_conflict'}
        conn.execute('''INSERT INTO proxy_sponsor_registrations(proxy_node_id,engine,ad_tag,registered_by_user_id)
            VALUES(%s,'official_mtproxy',%s,%s) ON CONFLICT(proxy_node_id) DO UPDATE SET
            ad_tag=EXCLUDED.ad_tag,registered_by_user_id=EXCLUDED.registered_by_user_id,registered_at=now()''',
            (job['proxy_node_id'],tag,job['owner_id']))
        conn.execute("UPDATE proxy_nodes SET status='active',updated_at=now() WHERE id=%s",(job['proxy_node_id'],))
        conn.execute("UPDATE proxy_provisioning_jobs SET state='active',error_code=NULL,updated_at=now() WHERE id=%s",(job['id'],))
        event = 'proxy_created_for_reserve' if job['reason']=='reserve' else 'proxy_created_by_ai'
        conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,%s,jsonb_build_object('node_id',%s::text))",(job['owner_id'],event,str(job['proxy_node_id'])))
        return {'accepted':True}
    if request['action'] != 'claim':
        raise ValueError('invalid_action')
    if request.get('resources_ok') is not True:
        return {'state':'wait','reason':'resources_low'}
    if job:
        if job['host'] != host or job['policy_revision'] != cfg['revision'] or job['archived_at']:
            return {'state':'wait','reason':'policy_changed'}
        if job['attempts'] >= 5 and job['retry_at'] <= datetime.now(timezone.utc):
            conn.execute("UPDATE proxy_provisioning_jobs SET state='failed',error_code=COALESCE(error_code,'preparation_interrupted'),updated_at=now() WHERE id=%s",(job['id'],))
            return {'state':'wait','reason':'preparation_interrupted'}
        if job['attempts'] >= 5 or job['retry_at'] > datetime.now(timezone.utc):
            return {'state':'wait','reason':'retry_later'}
    else:
        count = conn.execute('SELECT count(*) AS n,max(created_at) AS last FROM proxy_provisioning_jobs').fetchone()
        if count['n'] >= cfg['max_instances'] or conn.execute("SELECT 1 FROM proxy_provisioning_jobs WHERE state='failed'").fetchone():
            return {'state':'wait','reason':'limit_reached'}
        now = datetime.now(timezone.utc)
        if count['last'] and now-count['last'] < timedelta(days=1):
            return {'state':'wait','reason':'daily_limit'}
        # Резерв — явное решение владельца. Не вызываем модель и не отпускаем
        # общую блокировку до записи задания: два агента не создадут два резерва.
        reason = 'reserve' if cfg['reserve_enabled'] and count['n']==0 else 'demand'
        if reason == 'reserve':
            conn.execute("UPDATE proxy_provisioning_settings SET decision='create_reserve' WHERE singleton")
        else:
            if cfg['evaluated_at'] and now-cfg['evaluated_at'] < timedelta(minutes=15):
                return {'state':'wait','reason':'evaluation_cooldown'}
            conn.execute("UPDATE proxy_provisioning_settings SET evaluated_at=now(),decision='checking' WHERE singleton")
            # Короткий запрос модели проходит после сохранения ограничения частоты.
            conn.commit()
            aliases = host_aliases(request)
            delayed = demand(conn,host,cfg['wait_minutes'],aliases)
            config = channel_ai_connection.active('proxy_provisioning')
            decision = 'no_demand'
            if delayed and config and request.get('resources_ok') is True:
                try:
                    decision = advise({'delayed_campaigns':delayed,'wait_minutes':cfg['wait_minutes'],
                        'extra_instances':count['n'],'maximum':cfg['max_instances'],'resources_available':True},config)
                except (OSError,ValueError,TypeError,KeyError,IndexError):
                    decision = 'ai_unavailable'
            conn.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
            current = configuration(conn)
            if current['revision'] != cfg['revision'] or not allowed(conn,current) or config != channel_ai_connection.active('proxy_provisioning'):
                return {'state':'wait','reason':'policy_changed'}
            if decision == 'create_proxy' and not demand(conn,host,cfg['wait_minutes'],aliases):
                decision = 'no_demand'
            conn.execute('UPDATE proxy_provisioning_settings SET decision=%s WHERE singleton',(decision,))
            if decision != 'create_proxy':
                return {'state':'wait','reason':decision}
        if conn.execute("SELECT 1 FROM proxy_provisioning_jobs WHERE state<>'active' OR created_at>now()-interval '1 day'").fetchone():
            return {'state':'wait'}
        ports = request.get('free_ports',[])
        reserved = reserved_ports(conn,host,host_aliases(request))
        port = next((p for p in range(cfg['first_port'],cfg['first_port']+cfg['max_instances'])
            if p in ports and p not in reserved),None)
        if not port:
            return {'state':'wait','reason':'port_busy'}
        node_id = uuid4()
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port,status,target_engine,engine_selection_mode) VALUES(%s,%s,%s,%s,'paused','official_mtproxy','auto')",(node_id,'MTProxy · Авто '+str(port),host,port))
        job = conn.execute('''INSERT INTO proxy_provisioning_jobs(proxy_node_id,owner_id,policy_revision,reason)
            VALUES(%s,%s,%s,%s) RETURNING *''',(node_id,cfg['updated_by'],cfg['revision'],reason)).fetchone() | {'host':host,'port':port}
    conn.execute("UPDATE proxy_provisioning_jobs SET attempts=attempts+1,error_code=NULL,retry_at=now()+interval '15 minutes',updated_at=now() WHERE id=%s",(job['id'],))
    return {'state':'provision','job_id':str(job['id']),'node_id':str(job['proxy_node_id']),
            'host':host,'port':job['port']}


def main():
    import os, sys
    request = json.loads(sys.stdin.read(8192))
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=3,row_factory=dict_row,
                         options='-c statement_timeout=10000') as conn:
        result = handle(conn,request)
    print(json.dumps(result,default=str))


if __name__ == '__main__':
    main()
