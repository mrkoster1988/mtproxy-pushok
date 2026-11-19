"""Доска почёта: календарные периоды Москвы, фиксированные итоги и бонусы без дублей."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .user_visibility import can_view_username, hide_username
from .wallet_ledger import post
from .channel_logos import user_avatar

KINDS = ('month', 'year')
METRICS = {'invited': 'Приглашённые пользователи', 'paid_referrals': 'Платящие приглашённые',
           'spend': 'Расходы на рекламу',
           'paid_campaigns': 'Оплаченные кампании'}
MOSCOW = ZoneInfo('Europe/Moscow')
Kind = Literal['month', 'year']
Metric = Literal['invited', 'paid_referrals', 'spend', 'spend_rub', 'spend_xtr', 'paid_campaigns']


def bounds(kind, at):
    start = at.astimezone(MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == 'month':
        start = start.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif kind == 'year':
        start = start.replace(month=1, day=1); end = start.replace(year=start.year+1)
    else:
        raise ValueError('invalid_period')
    return start, end


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    top_size: int = Field(default=50, ge=3, le=200, strict=True)
    # Копейки бонусных рублей, до 10 мест на каждую номинацию и период.
    rewards: dict[str, dict[str, list[int]]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_rewards(self):
        if any(k not in KINDS for k in self.rewards): raise ValueError('invalid_period')
        total = 0
        for categories in self.rewards.values():
            for metric, amounts in categories.items():
                if metric not in {*METRICS, 'spend_rub', 'spend_xtr'} or len(amounts)>10: raise ValueError('invalid_metric_or_places')
                maximum=200000000 if metric=='spend' else 100000000
                if any(type(a) is not int or a<0 or a>maximum for a in amounts): raise ValueError('invalid_reward')
                total += sum(amounts)
        if total>1000000000: raise ValueError('reward_budget_exceeded')
        return self


# Источник денег: подтверждённые реальные оплаты; бонусы и ручные отметки не считаются.
PAYMENTS = '''WITH payments AS (
 SELECT c.id campaign_id,c.advertiser_user_id user_id,p.currency,(p.amount-c.refunded_amount)::bigint amount,p.settled_at paid_at, CASE WHEN p.currency='RUB' THEN p.amount-c.refunded_amount ELSE round(c.price_kopecks_snapshot::numeric*(p.amount-c.refunded_amount)/NULLIF(p.amount,0)) END::bigint rub_amount
 FROM payment_attempts p JOIN campaigns c ON c.id=p.campaign_id
 WHERE p.status='succeeded' AND p.amount>c.refunded_amount AND ((p.provider='telegram_stars' AND NOT p.telegram_test_environment) OR (p.provider='sbp' AND p.sbp_test_mode IS FALSE)) AND c.payment_status='verified'
 UNION ALL
 SELECT c.id,c.advertiser_user_id,w.currency,w.cash_amount-w.refunded_cash,w.created_at,CASE WHEN w.currency='RUB' THEN w.cash_amount-w.refunded_cash ELSE round(c.price_kopecks_snapshot::numeric*(w.cash_amount-w.refunded_cash)/NULLIF(w.cash_amount+w.bonus_amount,0)) END::bigint
 FROM wallet_payments w JOIN campaigns c ON c.id=w.campaign_id
 WHERE w.status='paid' AND w.cash_amount>w.refunded_cash AND c.payment_status='verified'
 AND NOT EXISTS(SELECT 1 FROM payment_attempts p WHERE p.campaign_id=c.id AND p.status='succeeded')
), eligible AS (SELECT id FROM users WHERE NOT is_developer AND blocked_at IS NULL) '''


def scores(conn, metric, start, end, limit, viewer=None):
    if metric == 'invited':
        query = '''SELECT r.inviter_user_id user_id,count(*) score,min(r.created_at) reached_at
            FROM growth_referrals r JOIN eligible e ON e.id=r.referred_user_id
            WHERE r.created_at >= %s AND r.created_at < %s GROUP BY r.inviter_user_id'''
    elif metric == 'paid_referrals':
        query = '''SELECT r.inviter_user_id user_id,count(*) score,min(p.first_paid) reached_at
            FROM growth_referrals r JOIN (SELECT user_id,min(paid_at) first_paid FROM payments GROUP BY user_id) p
            ON p.user_id=r.referred_user_id JOIN eligible e ON e.id=r.referred_user_id
            WHERE p.first_paid >= %s AND p.first_paid < %s AND r.created_at<=p.first_paid
            GROUP BY r.inviter_user_id'''
    else:
        value = 'count(DISTINCT campaign_id)' if metric=='paid_campaigns' else 'sum(rub_amount)' if metric=='spend' else 'sum(amount)'
        currency = " AND currency='RUB'" if metric=='spend_rub' else " AND currency='XTR'" if metric=='spend_xtr' else ''
        query = f'''SELECT user_id,{value} score,min(paid_at) reached_at FROM payments
            WHERE paid_at >= %s AND paid_at < %s {currency} GROUP BY user_id'''
    return conn.execute(PAYMENTS+f''', scored AS ({query}), ranked AS (
        SELECT count(*) OVER()::int participants,sum(s.score) OVER()::bigint score_total,s.user_id,s.score::bigint,u.first_name,u.last_name,u.username,
        row_number() OVER(ORDER BY s.score DESC,s.reached_at,s.user_id)::int rank
        FROM scored s JOIN users u ON u.id=s.user_id JOIN eligible e ON e.id=s.user_id
        WHERE s.score>0) SELECT * FROM ranked WHERE rank<=%s OR user_id=%s ORDER BY rank''',(start,end,limit,viewer)).fetchall()


def counting_start(conn, settings):
    first = conn.execute('SELECT min(created_at) first FROM users').fetchone()['first']
    return first or settings['started_at']


def settle(conn, now=None):
    now = now or datetime.now(timezone.utc)
    if not conn.execute('SELECT pg_try_advisory_xact_lock(782341,158) acquired').fetchone()['acquired']:
        return 0
    settings = conn.execute('SELECT * FROM hall_settings WHERE singleton FOR UPDATE').fetchone()
    since = counting_start(conn, settings)
    conn.execute('UPDATE hall_settings SET started_at=%s WHERE singleton', (since,))
    closed = 0

    def ensure(kind, start, end):
        rewards = {} if end <= settings['rewards_started_at'] else settings['rewards']
        return conn.execute('''INSERT INTO hall_periods(kind,starts_at,ends_at,settings) VALUES(%s,%s,%s,%s)
            ON CONFLICT(kind,starts_at) DO UPDATE SET kind=EXCLUDED.kind RETURNING *''',
            (kind,start,end,Jsonb({'top_size':settings['top_size'],'rewards':rewards}))).fetchone()

    def finish(row):
        nonlocal closed
        if row['closed_at'] is not None or row['ends_at'] > now: return
        cfg=row['settings'];start=row['starts_at'];end=row['ends_at'];kind=row['kind']
        for metric in dict.fromkeys([*METRICS, *cfg['rewards'].get(kind,{})]):
            prizes=cfg['rewards'].get(kind,{}).get(metric,[]) if end>settings['rewards_started_at'] else []
            for item in scores(conn,metric,max(start,since),end,cfg['top_size']):
                rank=item['rank'];amount=prizes[rank-1] if rank<=len(prizes) else 0
                entry=None
                if amount:
                    entry=post(conn,item['user_id'],'BONUS',amount,'gift_credit',
                        f"hall:{row['id']}:{metric}:{item['user_id']}",
                        f"Доска почёта · {METRICS.get(metric,metric)} · место {rank} · {start.astimezone(MOSCOW).date()}")['id']
                conn.execute('''INSERT INTO hall_results(period_id,metric,user_id,rank,score,bonus_amount,entry_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)''',(row['id'],metric,item['user_id'],rank,item['score'],amount,entry))
        conn.execute('UPDATE hall_periods SET closed_at=%s WHERE id=%s',(now,row['id']))
        closed+=1

    for kind in KINDS:
        # Текущие правила фиксируются сразу, независимо от размера исторического архива.
        ensure(kind,*bounds(kind,now))
        for row in conn.execute('''SELECT * FROM hall_periods WHERE kind=%s AND closed_at IS NULL
                AND ends_at<=%s ORDER BY ends_at LIMIT 24''',(kind,now)).fetchall():
            finish(row)
        first,_=bounds(kind,since)
        cursor=conn.execute('''INSERT INTO hall_history_cursor(kind,next_start) VALUES(%s,%s)
            ON CONFLICT(kind) DO NOTHING RETURNING next_start''',(kind,first)).fetchone()
        if not cursor:cursor=conn.execute('SELECT next_start FROM hall_history_cursor WHERE kind=%s',(kind,)).fetchone()
        start,end=bounds(kind,cursor['next_start'])
        # Пропуски до уже существующих новых итогов тоже заполняются; повторы не платят снова.
        for _ in range(24):
            if start>now: break
            row=ensure(kind,start,end)
            if end>now: break
            finish(row)
            start,end=bounds(kind,end)
        conn.execute('UPDATE hall_history_cursor SET next_start=%s WHERE kind=%s',(start,kind))
    return closed


def service_once(dsn):
    with psycopg.connect(dsn,connect_timeout=3,row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return {'closed':settle(conn)}


def medal_history(conn, uid):
    return conn.execute('''SELECT r.metric,r.rank,r.score,r.bonus_amount,p.starts_at,p.ends_at
        FROM hall_results r JOIN hall_periods p ON p.id=r.period_id
        WHERE r.user_id=%s AND r.rank<=3 AND p.kind='month' AND p.closed_at IS NOT NULL
        ORDER BY p.starts_at DESC,r.rank,r.metric LIMIT 120''',(uid,)).fetchall()


def router_for(dsn,current_user,require_admin):
    router=APIRouter(prefix='/api/v1/hall',tags=['hall'])
    def db(fn):
        try:
            with psycopg.connect(dsn(),connect_timeout=3,row_factory=dict_row) as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                return fn(conn)
        except psycopg.Error as exc: raise HTTPException(503,'database_unavailable') from exc

    @router.get('')
    def board(response: Response,kind:Kind='month',metric:Metric='invited',period_id:int|None=Query(None,ge=1),user=Depends(current_user)):
        response.headers['Cache-Control']='private, no-store'
        def read(conn):
            cfg=conn.execute('SELECT * FROM hall_settings WHERE singleton').fetchone()
            cfg['started_at']=counting_start(conn,cfg)
            periods=conn.execute('SELECT id,starts_at,ends_at FROM hall_periods WHERE kind=%s AND closed_at IS NOT NULL ORDER BY starts_at DESC LIMIT 60',(kind,)).fetchall()
            if period_id:
                period=conn.execute('SELECT * FROM hall_periods WHERE id=%s AND kind=%s AND closed_at IS NOT NULL',(period_id,kind)).fetchone()
                if not period: raise HTTPException(404,'hall_period_not_found')
                rows=conn.execute('''SELECT r.user_id,r.rank,r.score,r.bonus_amount,u.first_name,u.last_name,u.username
                    FROM hall_results r JOIN users u ON u.id=r.user_id WHERE r.period_id=%s AND r.metric=%s ORDER BY rank''',(period_id,metric)).fetchall()
                start,end=period['starts_at'],period['ends_at']; effective=period['settings']
            else:
                start,end=bounds(kind,datetime.now(timezone.utc))
                period=conn.execute('SELECT settings FROM hall_periods WHERE kind=%s AND starts_at=%s',(kind,start)).fetchone()
                effective=period['settings'] if period else cfg
                rows=scores(conn,metric,max(start,cfg['started_at']),end,effective['top_size'],user['id'])
            legacy_spend={}
            if period_id and metric=='spend' and not rows:
                # Старые итоги в разных валютах нельзя пересчитать из живых оплат:
                # возвраты/блокировки изменят уже закрытый рейтинг.
                for old in ('spend_rub','spend_xtr'):
                    saved=conn.execute('''SELECT r.user_id,r.rank,r.score,r.bonus_amount,u.first_name,u.last_name,u.username
                        FROM hall_results r JOIN users u ON u.id=r.user_id
                        WHERE r.period_id=%s AND r.metric=%s ORDER BY rank''',(period_id,old)).fetchall()
                    if saved:legacy_spend[old]=saved
            if not can_view_username(user):
                for group in (rows, *legacy_spend.values()):
                    for row in group:
                        hide_username(row)
            own=next((r for r in rows if str(r['user_id'])==str(user['id'])),None)
            return dict(rows=rows[:effective['top_size']],own=own,periods=periods,starts_at=start,ends_at=end,closed=bool(period_id),top_size=effective['top_size'],
                        rewards=effective['rewards'].get(kind,{}).get(metric,[]),metrics=METRICS,started_at=cfg['started_at'],legacy_spend=legacy_spend)
        return db(read)

    @router.get('/avatar/{uid}')
    def avatar(uid:str,user=Depends(current_user)):
        from uuid import UUID
        try: value=UUID(uid)
        except ValueError: raise HTTPException(404,'user_not_found')
        def find(conn):
            row=conn.execute("SELECT telegram_user_id FROM users WHERE id=%s AND NOT is_developer AND blocked_at IS NULL",(value,)).fetchone()
            if not row: raise HTTPException(404,'user_not_found')
            return row['telegram_user_id']
        data=user_avatar(db(find))
        return Response(data,status_code=200 if data else 204,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})

    @router.get('/admin')
    def admin(user=Depends(require_admin)):
        def read(conn):
            cfg=conn.execute('SELECT top_size,rewards,started_at,rewards_started_at FROM hall_settings WHERE singleton').fetchone()
            cfg['started_at']=counting_start(conn,cfg)
            totals=conn.execute('''SELECT count(DISTINCT user_id) FILTER(WHERE rank<=3) winners,count(*) FILTER(WHERE bonus_amount>0) awards,coalesce(sum(bonus_amount),0) bonus
                FROM hall_results r JOIN hall_periods p ON p.id=r.period_id WHERE p.kind IN ('month','year')''').fetchone()
            history=conn.execute('''SELECT p.id,p.kind,p.starts_at,p.ends_at,p.closed_at,count(r.user_id) places,
                coalesce(sum(r.bonus_amount),0) bonus FROM hall_periods p LEFT JOIN hall_results r ON r.period_id=p.id
                WHERE p.kind IN ('month','year') GROUP BY p.id ORDER BY p.starts_at DESC,p.kind LIMIT 100''').fetchall()
            start,end=bounds('month',datetime.now(timezone.utc))
            activity={}
            for metric in METRICS:
                rows=scores(conn,metric,max(start,cfg['started_at']),end,1)
                activity[metric]={'participants':rows[0]['participants'] if rows else 0,'total':rows[0]['score_total'] if rows else 0}
            return dict(settings=cfg,totals=totals,history=history,activity=activity)
        return db(read)

    @router.put('/admin/settings')
    def save(payload:Settings,user=Depends(require_admin)):
        def update(conn):
            # Сначала фиксируем текущие правила. Новые награды применяются со следующего периода.
            settle(conn)
            conn.execute('UPDATE hall_settings SET top_size=%s,rewards=%s WHERE singleton',(payload.top_size,Jsonb(payload.rewards)))
            conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'hall_settings_changed',%s)",(user['id'],Jsonb(payload.model_dump())))
            return payload.model_dump()
        return db(update)
    return router
