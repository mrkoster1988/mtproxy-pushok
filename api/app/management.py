"""Изменение объектов, снятие будущих слотов и архив вместо потери истории."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.errors import ExclusionViolation, UniqueViolation
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict


class ManagementInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ChannelEdit(ManagementInput):
    title: str = Field(min_length=1, max_length=255)
    telegram_url: str = Field(min_length=10, max_length=255)


class NodeEdit(ManagementInput):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)


class DateEdit(ManagementInput):
    requested_start_at: datetime | None = None
    tariff_id: UUID | None = None


class SlotEdit(ManagementInput):
    proxy_node_id: UUID
    start_at: datetime


def router_for(get_dsn, get_user, admin_dependency, operator_dependency, normalize_host):
    router = APIRouter(prefix='/api/v1', tags=['management'])

    @contextmanager
    def transaction():
        try:
            with psycopg.connect(get_dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                yield conn
        except ExclusionViolation as exc:
            raise HTTPException(409, 'proxy_node_slot_unavailable') from exc
        except UniqueViolation as exc:
            raise HTTPException(409, 'management_object_already_exists') from exc
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    def event(conn, user, kind, obj, campaign=None):
        conn.execute('INSERT INTO audit_events(actor_user_id,campaign_id,event_type,details_json) VALUES(%s,%s,%s,%s::jsonb)',
                     (user['id'], campaign, kind, json.dumps(obj, default=str)))

    def owned_campaign(conn, cid, user, operator=False):
        c = conn.execute('SELECT * FROM campaigns WHERE id=%s FOR UPDATE', (cid,)).fetchone()
        if not c or (not operator and c['advertiser_user_id'] != user['id']):
            raise HTTPException(404, 'campaign_not_found')
        return c

    def channel_unused(conn, cid, editing=True):
        historical = "" if editing else " AND placement_status NOT IN ('completed','cancelled')"
        if conn.execute("SELECT 1 FROM campaigns WHERE channel_id=%s AND archived_at IS NULL AND moderation_status NOT IN ('draft','changes_requested','rejected')" + historical + " LIMIT 1", (cid,)).fetchone():
            raise HTTPException(409, 'channel_in_review_or_approved')

    @router.patch('/channels/{channel_id}')
    def edit_channel(channel_id: UUID, payload: ChannelEdit, user=Depends(get_user)):
        import re
        match = re.fullmatch(r'https://(?:t\.me|telegram\.me)/([a-zA-Z][a-zA-Z0-9_]{3,31})/?', payload.telegram_url.strip())
        if not match or not payload.title.strip():
            raise HTTPException(422, 'invalid_telegram_channel_url')
        with transaction() as conn:
            ch = conn.execute('SELECT id FROM sponsor_channels WHERE id=%s AND owner_user_id=%s AND archived_at IS NULL FOR UPDATE', (channel_id, user['id'])).fetchone()
            if not ch:
                raise HTTPException(404, 'channel_not_found')
            channel_unused(conn, channel_id)
            row = conn.execute('UPDATE sponsor_channels SET title=%s,telegram_channel_id=CASE WHEN telegram_url IS DISTINCT FROM %s THEN NULL ELSE telegram_channel_id END,telegram_url=%s WHERE id=%s RETURNING id,title,telegram_url',
                               (payload.title.strip(), 'https://t.me/'+match[1].lower(), 'https://t.me/'+match[1].lower(), channel_id)).fetchone()
            event(conn, user, 'channel_updated', {'channel_id': channel_id})
            return row

    @router.delete('/channels/{channel_id}')
    def archive_channel(channel_id: UUID, user=Depends(get_user)):
        with transaction() as conn:
            ch = conn.execute('SELECT * FROM sponsor_channels WHERE id=%s AND owner_user_id=%s FOR UPDATE', (channel_id, user['id'])).fetchone()
            if not ch:
                raise HTTPException(404, 'channel_not_found')
            conn.execute('UPDATE sponsor_channels SET archived_at=COALESCE(archived_at,now()) WHERE id=%s', (channel_id,))
            event(conn, user, 'channel_archived', {'channel_id': channel_id})
        return {'archived': True}

    @router.patch('/campaigns/{campaign_id}/date')
    @router.patch('/campaigns/{campaign_id}')
    def edit_date(campaign_id: UUID, payload: DateEdit, user=Depends(get_user)):
        start = payload.requested_start_at
        if start and (start.tzinfo is None or start <= datetime.now(timezone.utc)):
            raise HTTPException(422, 'schedule_start_must_be_future')
        with transaction() as conn:
            c = owned_campaign(conn, campaign_id, user)
            if c['archived_at'] or c['moderation_status'] not in ('draft','changes_requested'):
                raise HTTPException(409, 'campaign_not_editable')
            if conn.execute("SELECT 1 FROM payment_attempts WHERE campaign_id=%s LIMIT 1",(campaign_id,)).fetchone():
                raise HTTPException(409,'management_payment_in_use')
            if 'requested_start_at' in payload.model_fields_set:
                conn.execute('UPDATE campaigns SET requested_start_at=%s,updated_at=now() WHERE id=%s', (start, campaign_id))
            if payload.tariff_id:
                actor=conn.execute('SELECT role FROM users WHERE id=%s FOR SHARE',(user['id'],)).fetchone()
                tariff=conn.execute('SELECT * FROM tariffs WHERE id=%s AND is_active AND archived_at IS NULL FOR SHARE',(payload.tariff_id,)).fetchone()
                if not tariff or (tariff['admin_only'] and actor['role']!='admin'):
                    raise HTTPException(404,'active_tariff_not_found')
                conn.execute('UPDATE campaigns SET tariff_id=%s,tariff_name_snapshot=%s,duration_seconds_snapshot=%s,price_kopecks_snapshot=%s,currency_snapshot=%s,updated_at=now() WHERE id=%s',
                             (tariff['id'],tariff['name'],tariff['duration_seconds'],tariff['price_kopecks'],tariff['currency'],campaign_id))
            event(conn, user, 'campaign_date_updated', {'requested_start_at': start}, campaign_id)
        return {'updated': True}

    @router.post('/campaigns/{campaign_id}/recall')
    def recall(campaign_id: UUID,user=Depends(get_user)):
        with transaction() as conn:
            c=owned_campaign(conn,campaign_id,user)
            if c['archived_at'] or c['moderation_status']!='pending' or c['placement_status']!='not_scheduled' or c['payment_status']!='not_required':
                raise HTTPException(409,'campaign_not_editable')
            if conn.execute('SELECT 1 FROM payment_attempts WHERE campaign_id=%s LIMIT 1',(campaign_id,)).fetchone():
                raise HTTPException(409,'management_payment_in_use')
            conn.execute("UPDATE campaigns SET moderation_status='draft',updated_at=now() WHERE id=%s",(campaign_id,))
            event(conn,user,'campaign_recalled',{},campaign_id)
        return {'recalled':True}

    @router.delete('/campaigns/{campaign_id}')
    def archive_campaign(campaign_id: UUID, user=Depends(get_user)):
        with transaction() as conn:
            c = owned_campaign(conn, campaign_id, user, user['role'] in ('admin','operator'))
            if c['archived_at']:
                return {'archived': True}
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s', (campaign_id,))
            # Платёжные попытки и активный показ продолжают учитываться оператором.
            conn.execute("""UPDATE campaigns SET archived_at=now(),auto_schedule_paused=true,
                archived_state=jsonb_build_object('placement_status',CASE WHEN placement_status='scheduled' THEN 'not_scheduled' ELSE placement_status END),
                placement_status=CASE WHEN placement_status='scheduled' THEN 'cancelled' ELSE placement_status END,
                updated_at=now() WHERE id=%s""", (campaign_id,))
            event(conn, user, 'campaign_archived', {}, campaign_id)
        return {'archived': True}

    @router.patch('/admin/proxy-nodes/{node_id}')
    def edit_node(node_id: UUID, payload: NodeEdit, user=Depends(admin_dependency)):
        host = normalize_host(payload.host)
        with transaction() as conn:
            n = conn.execute('SELECT * FROM proxy_nodes WHERE id=%s AND archived_at IS NULL FOR UPDATE', (node_id,)).fetchone()
            if not n:
                raise HTTPException(404, 'proxy_node_not_found')
            if not payload.name.strip():
                raise HTTPException(422, 'proxy_name_required')
            if host != n['host'] or payload.port != n['port']:
                node_free(conn, node_id)
            conn.execute('UPDATE proxy_nodes SET name=%s,host=%s,port=%s,updated_at=now() WHERE id=%s', (payload.name.strip(),host,payload.port,node_id))
            event(conn, user, 'proxy_node_updated', {'node_id': node_id})
        return {'updated': True, 'changes_applied': False}

    def node_free(conn, node_id):
        if conn.execute("SELECT 1 FROM campaigns WHERE proxy_node_id=%s AND placement_status IN ('scheduled','active') LIMIT 1", (node_id,)).fetchone() or conn.execute('SELECT 1 FROM slot_holds WHERE proxy_node_id=%s AND expires_at>now() LIMIT 1', (node_id,)).fetchone():
            raise HTTPException(409, 'proxy_node_has_placements')

    @router.delete('/admin/proxy-nodes/{node_id}')
    def archive_node(node_id: UUID, user=Depends(admin_dependency)):
        with transaction() as conn:
            n = conn.execute('SELECT id FROM proxy_nodes WHERE id=%s FOR UPDATE', (node_id,)).fetchone()
            if not n:
                raise HTTPException(404, 'proxy_node_not_found')
            conn.execute('DELETE FROM slot_holds WHERE proxy_node_id=%s',(node_id,))
            conn.execute("""UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,
                scheduled_start_at=NULL,scheduled_end_at=NULL,updated_at=now()
                WHERE proxy_node_id=%s AND placement_status='scheduled'""",(node_id,))
            conn.execute("""UPDATE proxy_nodes SET archived_at=COALESCE(archived_at,now()),
                status=CASE WHEN EXISTS(SELECT 1 FROM campaigns WHERE proxy_node_id=%s AND placement_status='active')
                THEN status ELSE 'paused' END,updated_at=now() WHERE id=%s""",(node_id,node_id))
            event(conn, user, 'proxy_node_archived', {'node_id': node_id})
        return {'archived': True, 'changes_applied': False}

    @router.patch('/operator/campaigns/{campaign_id}/slot')
    def move_slot(campaign_id: UUID, payload: SlotEdit, user=Depends(operator_dependency)):
        now = datetime.now(timezone.utc)
        if payload.start_at.tzinfo is None or payload.start_at <= now:
            raise HTTPException(422, 'schedule_start_must_be_future')
        with transaction() as conn:
            c = owned_campaign(conn, campaign_id, user, True)
            future_slot(conn, c, now)
            node = conn.execute("SELECT id FROM proxy_nodes WHERE id=%s AND status='active' AND archived_at IS NULL FOR UPDATE", (payload.proxy_node_id,)).fetchone()
            if not node:
                raise HTTPException(404, 'active_proxy_node_not_found')
            end = payload.start_at + timedelta(seconds=c['duration_seconds_snapshot'])
            hold = conn.execute("SELECT 1 FROM slot_holds WHERE proxy_node_id=%s AND campaign_id<>%s AND expires_at>now() AND tstzrange(starts_at,ends_at,'[)') && tstzrange(%s,%s,'[)') LIMIT 1", (payload.proxy_node_id,campaign_id,payload.start_at,end)).fetchone()
            if hold:
                raise HTTPException(409, 'slot_held_by_another_campaign')
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s', (campaign_id,))
            conn.execute('UPDATE campaigns SET proxy_node_id=%s,scheduled_start_at=%s,scheduled_end_at=%s,updated_at=now() WHERE id=%s', (payload.proxy_node_id,payload.start_at,end,campaign_id))
            event(conn, user, 'placement_rescheduled', {'node_id':payload.proxy_node_id,'start_at':payload.start_at}, campaign_id)
        return {'updated': True}

    @router.post('/operator/campaigns/{campaign_id}/auto-schedule')
    def resume_auto_schedule(campaign_id: UUID, user=Depends(operator_dependency)):
        with transaction() as conn:
            c=owned_campaign(conn,campaign_id,user,True)
            if c['archived_at'] or c['placement_status']!='not_scheduled' or c['moderation_status']!='approved' or c['payment_status']!='verified':
                raise HTTPException(409,'campaign_not_ready_for_placement')
            conn.execute('UPDATE campaigns SET auto_schedule_paused=false WHERE id=%s',(campaign_id,))
            event(conn,user,'auto_schedule_resumed',{},campaign_id)
        return {'queued':True}

    @router.get('/operator/campaigns/{campaign_id}/available-nodes')
    def available_nodes(campaign_id: UUID, start_at: datetime, user=Depends(operator_dependency)):
        if start_at.tzinfo is None or start_at <= datetime.now(timezone.utc):
            raise HTTPException(422, 'schedule_start_must_be_future')
        with transaction() as conn:
            c=owned_campaign(conn,campaign_id,user,True)
            if c['archived_at'] or c['moderation_status']!='approved' or c['payment_status']!='verified':
                raise HTTPException(409,'campaign_not_ready_for_placement')
            end=start_at+timedelta(seconds=c['duration_seconds_snapshot'])
            rows=conn.execute("""SELECT n.id,n.name FROM proxy_nodes n
                WHERE n.status='active' AND n.archived_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM campaigns other
                  WHERE other.proxy_node_id=n.id AND other.id<>%s
                  AND other.placement_status IN ('scheduled','active')
                  AND tstzrange(other.scheduled_start_at,other.scheduled_end_at,'[)') && tstzrange(%s,%s,'[)'))
                AND NOT EXISTS(SELECT 1 FROM slot_holds h
                  WHERE h.proxy_node_id=n.id AND h.campaign_id<>%s AND h.expires_at>now()
                  AND tstzrange(h.starts_at,h.ends_at,'[)') && tstzrange(%s,%s,'[)'))
                ORDER BY n.name""",(campaign_id,start_at,end,campaign_id,start_at,end)).fetchall()
        return {'nodes':rows,'starts_at':start_at,'ends_at':end}

    def future_slot(conn, c, now):
        if c['archived_at'] or c['placement_status']!='scheduled' or c['scheduled_start_at'] <= now:
            raise HTTPException(409, 'management_slot_already_started')
        if conn.execute('SELECT 1 FROM placement_observations WHERE campaign_id=%s LIMIT 1',(c['id'],)).fetchone():
            raise HTTPException(409, 'management_slot_has_observations')

    @router.delete('/operator/campaigns/{campaign_id}/slot')
    def remove_slot(campaign_id: UUID, user=Depends(operator_dependency)):
        with transaction() as conn:
            c = owned_campaign(conn, campaign_id, user, True)
            future_slot(conn, c, datetime.now(timezone.utc))
            conn.execute("UPDATE campaigns SET auto_schedule_paused=true,placement_status='not_scheduled',proxy_node_id=NULL,scheduled_start_at=NULL,scheduled_end_at=NULL,updated_at=now() WHERE id=%s", (campaign_id,))
            conn.execute('DELETE FROM slot_holds WHERE campaign_id=%s', (campaign_id,))
            event(conn, user, 'placement_slot_removed', {}, campaign_id)
        return {'removed': True, 'payment_unchanged': True}

    @router.delete('/admin/tariffs/{tariff_id}')
    def archive_tariff(tariff_id: UUID, user=Depends(admin_dependency)):
        with transaction() as conn:
            t = conn.execute('UPDATE tariffs SET archived_at=COALESCE(archived_at,now()),is_active=false WHERE id=%s RETURNING id', (tariff_id,)).fetchone()
            if not t:
                raise HTTPException(404, 'tariff_not_found')
            event(conn,user,'tariff_archived',{'tariff_id':tariff_id})
        return {'archived': True}

    tables={'channels':('sponsor_channels','title'), 'campaigns':('campaigns','tariff_name_snapshot'), 'nodes':('proxy_nodes','name'), 'tariffs':('tariffs','name')}

    @router.get('/management/archive')
    def archives(user=Depends(get_user)):
        result=[]
        with transaction() as conn:
            for kind,(table,title) in tables.items():
                if kind in ('nodes','tariffs') and user['role']!='admin':
                    continue
                condition=''
                params=[]
                if kind=='channels':condition=' AND owner_user_id=%s';params=[user['id']]
                if kind=='campaigns' and user['role'] not in ('operator','admin'):condition=' AND advertiser_user_id=%s';params=[user['id']]
                rows=conn.execute(f'SELECT id,{title} AS title,archived_at FROM {table} WHERE archived_at IS NOT NULL'+condition+' ORDER BY archived_at DESC LIMIT 100',params).fetchall()
                result.extend({**row,'kind':kind} for row in rows)
        return {'items':result}

    @router.post('/management/archive/{kind}/{object_id}/restore')
    def restore(kind: str, object_id: UUID, user=Depends(get_user)):
        if kind not in tables:
            raise HTTPException(404,'management_object_not_found')
        table=tables[kind][0]
        with transaction() as conn:
            row=conn.execute(f'SELECT * FROM {table} WHERE id=%s AND archived_at IS NOT NULL FOR UPDATE',(object_id,)).fetchone()
            if not row or (kind in ('nodes','tariffs') and user['role']!='admin') or (kind=='channels' and row['owner_user_id']!=user['id']) or (kind=='campaigns' and user['role'] not in ('admin','operator') and row['advertiser_user_id']!=user['id']):
                raise HTTPException(404,'management_object_not_found')
            if kind=='campaigns':
                dependencies=conn.execute('SELECT c.archived_at AS channel_archive,t.archived_at AS tariff_archive FROM sponsor_channels c,tariffs t WHERE c.id=%s AND t.id=%s FOR SHARE',(row['channel_id'],row['tariff_id'])).fetchone()
                if dependencies['channel_archive'] or dependencies['tariff_archive']:
                    raise HTTPException(409,'management_restore_dependencies')
                conn.execute("UPDATE campaigns SET archived_at=NULL,placement_status=COALESCE(archived_state->>'placement_status','not_scheduled'),archived_state=NULL,updated_at=now() WHERE id=%s",(object_id,))
            else:
                conn.execute(f'UPDATE {table} SET archived_at=NULL WHERE id=%s',(object_id,))
            event(conn,user,'management_object_restored',{'kind':kind,'id':object_id},object_id if kind=='campaigns' else None)
        return {'restored':True}

    return router
