"""Общий TCP-вход. Тема задаётся самим пользователем, профиль не передаётся узлам."""
import json
import os
import re
import time
from uuid import UUID, uuid4
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .channel_topics import LABELS
from . import proxy_link

# Порядок закреплён: порт тематического входа не меняется при новых темах.
TOPICS = ('technology','education','business','finance','marketing','career','news',
          'entertainment','gaming','culture','health','beauty','sports','travel','food',
          'real_estate','auto','family','pets','science','industry','agriculture',
          'shopping','lifestyle','other')
PRIVATE = {'Cache-Control': 'private, no-store'}


def local_hosts():
    """Адреса этого VPS из локальных файлов агента, без DNS и данных клиента."""
    hosts = set()
    direct = proxy_link.read_proxy_url(proxy_link.LINK_FILE)
    if direct:
        hosts.add(parse_qs(urlsplit(direct).query)['server'][0].lower())
    try:
        report = proxy_link.read_json(proxy_link.LINK_FILE.parent/'proxy-gateway-status.json')
        if (isinstance(report, dict) and type(report.get('checked_at')) in (int,float)
                and 0 <= time.time()-report['checked_at'] <= 120):
            values = report.get('hosts')
            if isinstance(values,list) and 1 <= len(values) <= 2 and all(
                    isinstance(h,str) and re.fullmatch(r'[a-zA-Z0-9.:-]{1,253}',h) for h in values):
                hosts.update(h.lower() for h in values)
    except (OSError, ValueError, TypeError):
        pass
    # Без отчёта порт всё равно проверяется на VPS перед применением HAProxy.
    return sorted(hosts)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    port: int = Field(ge=1024, le=65000, strict=True)
    interests_enabled: bool = Field(strict=True)
    revision: UUID


class Interest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    topic: str | None = Field(max_length=32)


def read_status():
    try:
        value = proxy_link.read_json(proxy_link.LINK_FILE.parent/'proxy-gateway-status.json', limit=8192)
        if not isinstance(value, dict) or type(value.get('checked_at')) not in (int, float):
            return None
        if not 0 <= time.time()-value['checked_at'] <= 120:
            return None
        return {k: value[k] for k in ('code','checked_at','revision','nodes','excluded','port','interests_enabled') if k in value}
    except (OSError, ValueError, TypeError):
        return None


def settings(conn):
    row = conn.execute('SELECT * FROM proxy_gateway_settings WHERE singleton').fetchone()
    status = read_status()
    if status:
        status['current'] = status.get('revision') == str(row['revision'])
    return dict(row, agent=status, topic_count=len(TOPICS))


def snapshot(conn, request):
    """Только host CLI; ключей, личных интересов и ID пользователей в ответе нет."""
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    cfg = conn.execute('SELECT * FROM proxy_gateway_settings WHERE singleton').fetchone()
    hosts = request.get('hosts')
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 2 or any(
            not isinstance(h, str) or not re.fullmatch(r'[a-zA-Z0-9.:-]{1,253}', h) for h in hosts):
        raise ValueError('invalid_host')
    owner = cfg['updated_by'] and conn.execute("SELECT 1 FROM users WHERE id=%s AND is_owner AND role='admin' AND blocked_at IS NULL", (cfg['updated_by'],)).fetchone()
    from .promotion_runtime import node_plan, confirmation
    nodes = conn.execute("""SELECT n.*,r.campaign_id,r.state,r.checked_at,r.plan_revision,c.topic
        FROM proxy_nodes n LEFT JOIN promotion_runtime r ON r.proxy_node_id=n.id
        LEFT JOIN campaigns c ON c.id=r.campaign_id
        WHERE n.host=ANY(%s) AND n.status='active' AND n.archived_at IS NULL
          AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy') ORDER BY n.id""", (hosts,)).fetchall()
    candidates = []
    for node in nodes:
        plan = node_plan(conn,node)
        topic = node['topic'] if plan['state']=='apply' and confirmation(conn,node,node,plan=plan)['config_applied'] else None
        candidates.append(dict(id=str(node['id']),host=node['host'],port=node['port'],topic=topic))
    return dict(enabled=bool(cfg['enabled'] and owner), port=cfg['port'],
                interests_enabled=cfg['interests_enabled'], revision=str(cfg['revision']),
                topics=list(TOPICS), nodes=candidates)


def router_for(dsn, current_user, require_owner):
    router = APIRouter(prefix='/api/v1', tags=['proxy-gateway'])

    def db(action):
        try:
            with psycopg.connect(dsn(), connect_timeout=3, row_factory=dict_row,
                                 options='-c statement_timeout=5000') as conn:
                return action(conn)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    @router.get('/admin/proxy-gateway')
    def read(response: Response, owner=Depends(require_owner)):
        response.headers.update(PRIVATE)
        return db(settings)

    @router.put('/admin/proxy-gateway')
    def save(data: Settings, response: Response, owner=Depends(require_owner)):
        response.headers.update(PRIVATE)
        def write(conn):
            from .proxy_provisioning import LOCK
            # Не резервируем порт одновременно с созданием нового узла.
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            cfg = conn.execute('SELECT * FROM proxy_gateway_settings WHERE singleton FOR UPDATE').fetchone()
            if cfg['revision'] != data.revision:
                raise HTTPException(409, 'gateway_settings_changed')
            last = data.port + (len(TOPICS) if data.interests_enabled else 0)
            if data.enabled and (data.port <= 20002 and last >= 19999 or conn.execute(
                    'SELECT 1 FROM proxy_nodes WHERE lower(host)=ANY(%s) AND port BETWEEN %s AND %s',
                    (local_hosts(), data.port, last)).fetchone()):
                raise HTTPException(409, 'gateway_port_conflict')
            # Смена адреса не должна незаметно ломать выданные постоянные ссылки.
            if cfg['enabled'] and data.enabled and (cfg['port'] != data.port or cfg['interests_enabled'] and not data.interests_enabled):
                raise HTTPException(409, 'gateway_disable_before_change')
            conn.execute('''UPDATE proxy_gateway_settings SET enabled=%s,port=%s,interests_enabled=%s,
                revision=%s,updated_by=%s,updated_at=now() WHERE singleton''',
                (data.enabled,data.port,data.interests_enabled,uuid4(),owner['id']))
            conn.execute('INSERT INTO proxy_gateway_changes(actor_id,settings) VALUES(%s,%s::jsonb)',
                         (owner['id'],json.dumps(data.model_dump(mode='json'))))
            return settings(conn)
        return db(write)

    def interest(conn, user):
        row = conn.execute('SELECT topic FROM user_proxy_interests WHERE user_id=%s', (user['id'],)).fetchone()
        return dict(topic=row['topic'] if row else None,
                    topics=[dict(id=t,label=LABELS[t]) for t in TOPICS])

    @router.get('/me/proxy-interest')
    def read_interest(response: Response, user=Depends(current_user)):
        response.headers.update(PRIVATE)
        return db(lambda conn: interest(conn,user))

    @router.put('/me/proxy-interest')
    def save_interest(data: Interest, response: Response, user=Depends(current_user)):
        response.headers.update(PRIVATE)
        if data.topic is not None and data.topic not in TOPICS:
            raise HTTPException(422,'proxy_interest_invalid')
        def write(conn):
            if data.topic is None:
                conn.execute('DELETE FROM user_proxy_interests WHERE user_id=%s', (user['id'],))
            else:
                conn.execute('''INSERT INTO user_proxy_interests(user_id,topic) VALUES(%s,%s)
                    ON CONFLICT(user_id) DO UPDATE SET topic=EXCLUDED.topic,updated_at=now()''', (user['id'],data.topic))
            return interest(conn,user)
        return db(write)

    @router.get('/me/proxy-connection')
    def connection(response: Response, user=Depends(current_user)):
        response.headers.update(PRIVATE)
        topic = db(lambda conn: interest(conn,user))['topic']
        url = proxy_link.current_gateway_url(topic) if topic else None
        return dict(url=url or proxy_link.current_proxy_url() or '', personalized=bool(url), topic=topic)

    return router


if __name__ == '__main__':
    import sys
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=3, row_factory=dict_row,
                         options='-c statement_timeout=5000') as connection:
        result = snapshot(connection,json.loads(sys.stdin.read(4096)))
    print(json.dumps(result,default=str))
