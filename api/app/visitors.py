"""Посещения авторизованного кабинета и доступный только администратору список."""
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict


class Visit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    visit_id: UUID


def router_for(get_dsn, get_user, require_admin):
    router = APIRouter(prefix='/api/v1')

    @router.post('/visits', status_code=204)
    def visit(data: Visit, user=Depends(get_user)):
        try:
            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute('''INSERT INTO app_visits(user_id,visit_id,surface) VALUES(%s,%s,%s)
                    ON CONFLICT(user_id,visit_id) DO UPDATE SET seen_at=now()
                    WHERE app_visits.seen_at < now()-interval '1 minute' ''',
                    (user['id'],data.visit_id,user.get('login_surface','web')))
        except psycopg.Error as exc:
            raise HTTPException(503,'database_unavailable') from exc

    @router.get('/admin/visitors')
    def visitors(response: Response, q: str=Query('',max_length=100), offset: int=Query(0,ge=0), admin=Depends(require_admin)):
        response.headers['Cache-Control']='private, no-store'
        try:
            with psycopg.connect(get_dsn(),row_factory=dict_row,connect_timeout=3) as conn:
                # Повторно проверяем роль при чтении персонального списка.
                actor=conn.execute('SELECT role FROM users WHERE id=%s',(admin['id'],)).fetchone()
                if not actor or actor['role']!='admin': raise HTTPException(403,'admin_role_required')
                rows=conn.execute('''WITH visits AS (
                    SELECT user_id,min(opened_at) first_visit,max(seen_at) last_visit,count(*) visits,
                    bool_or(surface='telegram') miniapp FROM app_visits GROUP BY user_id)
                    SELECT u.telegram_user_id,u.first_name,u.last_name,u.username,u.role,v.*,count(*) OVER() total
                    FROM visits v JOIN users u ON u.id=v.user_id
                    WHERE strpos(lower(concat_ws(' ',u.first_name,u.last_name,u.username,u.telegram_user_id::text)),lower(%s))>0
                    ORDER BY last_visit DESC,u.id LIMIT 100 OFFSET %s''',(q.strip().lstrip('@'),offset)).fetchall()
                return {'items':rows,'total':rows[0]['total'] if rows else 0,'offset':offset}
        except psycopg.Error as exc:
            raise HTTPException(503,'database_unavailable') from exc
    return router
