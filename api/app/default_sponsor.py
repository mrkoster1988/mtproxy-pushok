"""Канал свободного периода; секретные теги читаются только планировщиком агента."""
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

class DefaultSponsor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel_id: UUID | None = None


def fallback_plan(conn, node, waiting):
    # Не скрываем ошибку подготовки текущей кампании своим каналом.
    if waiting.get('campaign_id') and waiting.get('reason') not in ('period_ended','waiting_start'):
        return waiting
    row = conn.execute("""SELECT s.channel_id,ch.archived_at,b.ad_tag,
        b.channel_url=ch.telegram_url AND b.node_host=%s
        AND b.node_port=(SELECT port FROM proxy_nodes WHERE id=%s) AS current
        FROM default_sponsor_settings s JOIN sponsor_channels ch ON ch.id=s.channel_id
        LEFT JOIN channel_promotion_bindings b ON b.channel_id=s.channel_id AND b.proxy_node_id=%s
        WHERE s.singleton=TRUE AND NOT moderation_channel_blocked(ch)""",(node['host'],node['id'],node['id'])).fetchone()
    if not row:
        return waiting
    if row['archived_at'] or not row['ad_tag'] or not row['current']:
        return dict(state='idle',node_id=str(node['id']),reason='default_binding_missing')
    return dict(state='default',node_id=str(node['id']),reason='default_sponsor',
                default_channel_id=str(row['channel_id']),tag=row['ad_tag'])


def router_for(get_dsn, require_admin):
    router=APIRouter(prefix='/api/v1/admin/default-sponsor',tags=['admin'])
    @router.get('')
    def read(response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        try:
            with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:
                setting=conn.execute('SELECT channel_id FROM default_sponsor_settings WHERE singleton=TRUE').fetchone()
                channels=conn.execute('SELECT id,title,telegram_url FROM sponsor_channels WHERE archived_at IS NULL ORDER BY title,id').fetchall()
                nodes=conn.execute("SELECT id,name FROM proxy_nodes WHERE archived_at IS NULL AND status='active' AND (target_engine IS NULL OR target_engine='official_mtproxy') ORDER BY name,id").fetchall()
                prepared=[]
                if setting and setting['channel_id']:
                    prepared=conn.execute('''SELECT b.proxy_node_id FROM channel_promotion_bindings b
                        JOIN proxy_nodes n ON n.id=b.proxy_node_id JOIN sponsor_channels ch ON ch.id=b.channel_id
                        WHERE b.channel_id=%s AND ch.archived_at IS NULL AND n.archived_at IS NULL
                        AND b.channel_url=ch.telegram_url AND b.node_host=n.host AND b.node_port=n.port''',(setting['channel_id'],)).fetchall()
                return dict(channel_id=setting['channel_id'] if setting else None,channels=channels,nodes=nodes,prepared_node_ids=[r['proxy_node_id'] for r in prepared])
        except psycopg.Error as exc:
            raise HTTPException(503,'database_unavailable') from exc

    @router.put('')
    def save(payload: DefaultSponsor,response: Response,admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        try:
            with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:
                if payload.channel_id and not conn.execute('SELECT id FROM sponsor_channels WHERE id=%s AND archived_at IS NULL FOR SHARE',(payload.channel_id,)).fetchone():
                    raise HTTPException(404,'channel_not_found')
                conn.execute('''INSERT INTO default_sponsor_settings(singleton,channel_id,updated_by)
                    VALUES(TRUE,%s,%s) ON CONFLICT(singleton) DO UPDATE
                    SET channel_id=EXCLUDED.channel_id,updated_by=EXCLUDED.updated_by,updated_at=now()''',(payload.channel_id,admin['id']))
                conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'default_sponsor_changed',jsonb_build_object('channel_id',%s::text))",(admin['id'],str(payload.channel_id) if payload.channel_id else None))
                return {'saved':True,'channel_id':payload.channel_id}
        except psycopg.Error as exc:
            raise HTTPException(503,'database_unavailable') from exc
    return router
