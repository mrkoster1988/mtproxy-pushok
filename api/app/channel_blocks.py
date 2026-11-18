"""Чёрный список рекламных каналов. Снятие ограничения сохраняется в истории."""
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.rows import dict_row


def router_for(dsn, require_operator):
    router = APIRouter(prefix='/api/v1/operator/channel-blocks', tags=['operator'])

    @router.get('')
    def list_blocks(response: Response, page: int = Query(0, ge=0), operator=Depends(require_operator)):
        response.headers['Cache-Control'] = 'private, no-store'
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:
            rows = conn.execute('''SELECT b.id,b.telegram_url,b.telegram_channel_id::text,b.created_at,e.reason,e.category
                FROM channel_moderation_blocks b JOIN ai_moderation_events e ON e.id=b.event_id
                WHERE b.lifted_at IS NULL ORDER BY b.created_at DESC,b.id LIMIT 31 OFFSET %s''', (30*page,)).fetchall()
            return {'items': rows[:30], 'more': len(rows)>30}

    @router.delete('/{block_id}')
    def lift_block(block_id: UUID, operator=Depends(require_operator)):
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:
            actor = conn.execute('SELECT role,blocked_at FROM users WHERE id=%s FOR SHARE', (operator['id'],)).fetchone()
            if not actor or actor['blocked_at'] or actor['role'] not in ('admin','operator'):
                raise HTTPException(403,'operator_role_required')
            entry = conn.execute('SELECT telegram_channel_id FROM channel_moderation_blocks WHERE id=%s',(block_id,)).fetchone()
            if not entry:
                raise HTTPException(404,'channel_block_not_found')
            conn.execute('SELECT pg_advisory_xact_lock(%s::bigint)', (entry['telegram_channel_id'],))
            conn.execute('''UPDATE channel_moderation_blocks SET lifted_at=clock_timestamp(),lifted_by=%s
                WHERE id=%s AND lifted_at IS NULL''', (operator['id'], block_id))
            return {'lifted': True}

    return router
