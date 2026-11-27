"""Каталог и поиск существующих пользователей; доступ сотрудникам."""
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from .user_visibility import can_view_username, hide_username
from .telegram_environment import environment_name

USER_SORTS = {
    'recent': 'last_login_at DESC NULLS LAST, created_at DESC',
    'newest': 'created_at DESC',
    'oldest': 'created_at ASC',
    'name': "lower(coalesce(nullif(trim(concat_ws(' ',first_name,last_name)),''),nullif(username,''),telegram_user_id::text)) ASC"
}

DIRECTORY_CATEGORIES = {
    'user': "NOT is_developer AND role='advertiser' AND NOT has_launched_ads",
    'advertiser': "NOT is_developer AND role='advertiser' AND has_launched_ads",
    'operator': "NOT is_developer AND role='operator'",
    'admin': "NOT is_developer AND role='admin'",
    'developer': 'is_developer',
    'moderator': "NOT is_developer AND role='moderator'",
    'friends': 'is_friend',
    'tamada': 'is_tamada',
}


def router_for(dsn, require_operator):
    router = APIRouter(prefix='/api/v1/admin/users', tags=['admin'])

    @router.get('')
    def users(response: Response, query: str = Query(default='', max_length=100),
              role: str = '', visited: bool = False, sort: str = '', category: str = '',
              page: int = Query(default=0, ge=0, le=100000), admin=Depends(require_operator)):
        response.headers['Cache-Control'] = 'private, no-store'
        if role not in ('', 'admin', 'operator', 'moderator', 'advertiser'):
            raise HTTPException(422, 'invalid_user_role')
        if sort and sort not in USER_SORTS:
            raise HTTPException(422, 'invalid_user_sort')
        value = query.strip().lstrip('@')
        show_username = can_view_username(admin)
        order = USER_SORTS[sort or 'newest']
        if sort == 'name' and not show_username:
            order = order.replace("nullif(username,''),", '')
        ranking = ()
        if value and not sort and show_username:
            order = '(telegram_user_id::text=%s OR lower(username)=lower(%s)) DESC NULLS LAST, ' + order
            ranking = (value, value)
        where = """telegram_user_id>0 AND (%s='' OR telegram_user_id::text=%s OR id::text=%s
            OR (%s AND position(lower(%s) in lower(coalesce(username,'')))>0)
            OR position(lower(%s) in lower(concat_ws(' ',first_name,last_name)))>0)
            AND (%s='' OR role=%s) AND (NOT %s OR last_login_at IS NOT NULL)"""
        if category:
            if category not in DIRECTORY_CATEGORIES:
                raise HTTPException(422, 'invalid_user_category')
            where += ' AND (' + DIRECTORY_CATEGORIES[category] + ')'
        params = (value, value, value, show_username, value, value, role, role, visited)
        try:
            with psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                total = conn.execute('SELECT count(*) AS n FROM users WHERE '+where, params).fetchone()['n']
                rows = conn.execute('''SELECT id,telegram_user_id,first_name,last_name,username,role,is_owner,is_developer,is_friend,is_tamada,has_launched_ads,
                    created_at,last_login_at,blocked_at,
                    EXISTS(SELECT 1 FROM verified_profiles v WHERE v.user_id=users.id AND v.phone_verified_at IS NOT NULL AND v.telegram_environment=%s) AS phone_verified
                    FROM users WHERE '''+where+' ORDER BY '+order+', id LIMIT 20 OFFSET %s',
                    (environment_name(), *params, *ranking, page*20)).fetchall()
                if not show_username:
                    for row in rows:
                        hide_username(row)
                return dict(items=rows,total=total,page=page,page_size=20,more=(page+1)*20<total)
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc
    return router
