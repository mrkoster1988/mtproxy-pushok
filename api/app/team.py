"""Роли и блокировка: оператор управляет только неадминистративными аккаунтами."""
from typing import Literal
import psycopg
from .license_errors import explain as explain_license_error
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict


class TeamChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    telegram_user_id: int = Field(gt=0, lt=2**52)
    role: Literal['admin', 'operator', 'advertiser']


class AccessChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    telegram_user_id: int = Field(gt=0, lt=2**52)
    blocked: bool = Field(strict=True)


def router_for(get_dsn, require_admin, require_operator):
    router = APIRouter(prefix='/api/v1/admin/team', tags=['team'])

    def lock_users(conn, actor_id, telegram_id):
        # Единый порядок с назначением ролей защищает последнего администратора.
        conn.execute('SELECT pg_advisory_xact_lock(736102483)')
        actor = conn.execute('SELECT role,blocked_at FROM users WHERE id=%s FOR UPDATE', (actor_id,)).fetchone()
        if not actor or actor['blocked_at'] or actor['role'] not in ('admin','operator'):
            raise HTTPException(403, 'operator_role_required')
        target = conn.execute('SELECT id,role,blocked_at FROM users WHERE telegram_user_id=%s FOR UPDATE', (telegram_id,)).fetchone()
        if not target:raise HTTPException(404, 'team_user_must_login')
        if actor['role']=='operator' and target['role']=='admin':
            raise HTTPException(403, 'team_admin_protected')
        return actor,target

    def keep_admin(conn, target):
        if target['role']=='admin' and not target['blocked_at']:
            if conn.execute("SELECT count(*) AS n FROM users WHERE role='admin' AND blocked_at IS NULL").fetchone()['n']<=1:
                raise HTTPException(409, 'team_last_admin')

    @router.get('')
    def members(response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                return conn.execute("SELECT id,telegram_user_id,first_name,last_name,username,role,blocked_at FROM users WHERE role IN ('admin','operator') ORDER BY role,telegram_user_id").fetchall()
        except psycopg.Error as exc:
            explain_license_error(exc)
            raise HTTPException(503, 'database_unavailable') from exc

    @router.put('')
    def change(payload: TeamChange, response: Response, viewer=Depends(require_operator)):
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                actor,target=lock_users(conn,viewer['id'],payload.telegram_user_id)
                if actor['role']=='operator' and payload.role=='admin':
                    raise HTTPException(403, 'team_admin_grant_forbidden')
                if target['id']==viewer['id'] and payload.role!=actor['role']:
                    raise HTTPException(409, 'team_cannot_remove_self')
                if target['role']!=payload.role:
                    if payload.role!='admin':keep_admin(conn,target)
                    conn.execute('UPDATE users SET role=%s,updated_at=now() WHERE id=%s', (payload.role,target['id']))
                    conn.execute('DELETE FROM user_sessions WHERE user_id=%s', (target['id'],))
                    conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'team_role_changed',jsonb_build_object('telegram_user_id',%s::bigint,'old_role',%s::text,'new_role',%s::text))", (viewer['id'],payload.telegram_user_id,target['role'],payload.role))
                if payload.role in ('admin','operator') and not target['blocked_at']:
                    conn.execute('INSERT INTO bot_operator_subscriptions(operator_user_id) VALUES(%s) ON CONFLICT DO NOTHING', (target['id'],))
                else:
                    conn.execute('DELETE FROM bot_operator_subscriptions WHERE operator_user_id=%s', (target['id'],))
                return {'saved': True, 'telegram_user_id': payload.telegram_user_id, 'role': payload.role}
        except psycopg.Error as exc:
            explain_license_error(exc)
            raise HTTPException(503, 'database_unavailable') from exc

    @router.put('/access')
    def access(payload: AccessChange, response: Response, viewer=Depends(require_operator)):
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                actor,target=lock_users(conn,viewer['id'],payload.telegram_user_id)
                if target['id']==viewer['id']:raise HTTPException(409,'team_cannot_block_self')
                if payload.blocked:keep_admin(conn,target)
                if bool(target['blocked_at'])!=payload.blocked:
                    conn.execute('UPDATE users SET blocked_at=CASE WHEN %s THEN now() ELSE NULL END,updated_at=now() WHERE id=%s',(payload.blocked,target['id']))
                    # Отозванные сессии не оживают после разблокировки.
                    conn.execute('DELETE FROM user_sessions WHERE user_id=%s',(target['id'],))
                    if payload.blocked:
                        conn.execute('UPDATE wallet_preferences SET auto_pay=false WHERE user_id=%s',(target['id'],))
                        conn.execute('DELETE FROM bot_operator_subscriptions WHERE operator_user_id=%s',(target['id'],))
                    conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'user_access_changed',jsonb_build_object('telegram_user_id',%s::bigint,'blocked',%s::boolean))",(viewer['id'],payload.telegram_user_id,payload.blocked))
                return {'saved':True,'blocked':payload.blocked}
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc
    return router
