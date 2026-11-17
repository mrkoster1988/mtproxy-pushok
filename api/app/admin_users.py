"""Каталог и поиск существующих пользователей; доступ сотрудникам."""
import psycopg
from psycopg.rows import dict_row
from .telegram_environment import environment_name
from fastapi import APIRouter, Depends, HTTPException, Query, Response


def router_for(dsn, require_operator):
    router = APIRouter(prefix='/api/v1/admin/users', tags=['admin'])

    @router.get('')
    def users(response: Response, query: str = Query(default='', max_length=100),
              role: str = '', visited: bool = False,
              page: int = Query(default=0, ge=0, le=100000), admin=Depends(require_operator)):
        response.headers['Cache-Control'] = 'private, no-store'
        if role not in ('', 'admin', 'operator', 'advertiser'):
            raise HTTPException(422, 'invalid_user_role')
        value = query.strip().lstrip('@')
        where = """telegram_user_id>0 AND (%s='' OR telegram_user_id::text=%s OR id::text=%s
            OR position(lower(%s) in lower(coalesce(username,'')))>0
            OR position(lower(%s) in lower(concat_ws(' ',first_name,last_name)))>0)
            AND (%s='' OR role=%s) AND (NOT %s OR last_login_at IS NOT NULL)"""
        params = (value, value, value, value, value, role, role, visited)
        try:
            with psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                total = conn.execute('SELECT count(*) AS n FROM users WHERE '+where, params).fetchone()['n']
                rows = conn.execute('''SELECT id,telegram_user_id,first_name,last_name,username,role,
                    created_at,last_login_at,blocked_at,
                    EXISTS(SELECT 1 FROM verified_profiles v WHERE v.user_id=users.id AND v.phone_verified_at IS NOT NULL AND v.telegram_environment=%s) AS phone_verified
                    FROM users WHERE '''+where+'''
                    ORDER BY (telegram_user_id::text=%s OR lower(username)=lower(%s)) DESC NULLS LAST,
                    last_login_at DESC NULLS LAST,created_at DESC,id LIMIT 20 OFFSET %s''',
                    (environment_name(), *params, value, value, page*20)).fetchall()
                return dict(items=rows,total=total,page=page,page_size=20,more=(page+1)*20<total)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc
    return router
