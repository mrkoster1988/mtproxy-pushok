"""Управление ролями по точному Telegram ID, только действующим администратором."""
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

class TeamChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    telegram_user_id: int = Field(gt=0, lt=2**52)
    role: Literal['admin', 'operator', 'advertiser']


def router_for(get_dsn, require_admin):
    router = APIRouter(prefix='/api/v1/admin/team', tags=['admin'])

    @router.get('')
    def members(response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                return conn.execute("SELECT id,telegram_user_id,first_name,last_name,username,role FROM users WHERE role IN ('admin','operator') ORDER BY role,telegram_user_id").fetchall()
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc

    @router.put('')
    def change(payload: TeamChange, response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(736102483)')
                actor = conn.execute('SELECT role FROM users WHERE id=%s FOR UPDATE', (admin['id'],)).fetchone()
                if not actor or actor['role'] != 'admin':
                    raise HTTPException(403, 'admin_role_required')
                target = conn.execute('SELECT id,role FROM users WHERE telegram_user_id=%s FOR UPDATE', (payload.telegram_user_id,)).fetchone()
                if not target:
                    raise HTTPException(404, 'team_user_must_login')
                if target['id'] == admin['id'] and payload.role != 'admin':
                    raise HTTPException(409, 'team_cannot_remove_self')
                if target['role'] == 'admin' and payload.role != 'admin':
                    if conn.execute("SELECT count(*) AS n FROM users WHERE role='admin'").fetchone()['n'] <= 1:
                        raise HTTPException(409, 'team_last_admin')
                if target['role'] != payload.role:
                    conn.execute('UPDATE users SET role=%s,updated_at=now() WHERE id=%s', (payload.role,target['id']))
                    conn.execute('DELETE FROM user_sessions WHERE user_id=%s', (target['id'],))
                    conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'team_role_changed',jsonb_build_object('telegram_user_id',%s::bigint,'old_role',%s::text,'new_role',%s::text))", (admin['id'],payload.telegram_user_id,target['role'],payload.role))
                if payload.role in ('admin','operator'):
                    conn.execute('INSERT INTO bot_operator_subscriptions(operator_user_id) VALUES(%s) ON CONFLICT DO NOTHING', (target['id'],))
                else:
                    conn.execute('DELETE FROM bot_operator_subscriptions WHERE operator_user_id=%s', (target['id'],))
                return {'saved': True, 'telegram_user_id': payload.telegram_user_id, 'role': payload.role}
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc
    return router
