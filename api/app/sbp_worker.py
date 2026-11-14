"""Фоновый GET ожидающих СБП-платежей. Новые счета и размещения не создаёт."""
import argparse
import json
import os
import signal
import threading

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from .sbp_checkout import verify_background
from .sbp_provider import SBPProviderError
from .sbp_reconcile import SBPReconcileError
from .sbp_verification import SBPVerificationError

_LOCK = 736102481


def retry_seconds(failures):
    return min(3600, 60 * 2 ** min(max(failures - 1, 0), 6))


def service_once(dsn, *, apply=False, limit=20):
    """Один пакет. Состояние до сети фиксируется, сбой процесса повторится через минуту.

    Session advisory lock исключает одновременные worker; пользовательская сверка
    сериализуется существующей блокировкой кампании. Dry-run не обращается к банку.
    """
    if not 1 <= limit <= 100:
        raise ValueError('limit_out_of_range')
    result = dict(checked=0, pending=0, settled=0, retry=0, review=0, superseded=0)
    if apply and os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') != '1':
        return {**result, 'status': 'disabled'}
    with psycopg.connect(dsn, connect_timeout=3, autocommit=True, row_factory=dict_row,
                         options='-c statement_timeout=5000') as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (_LOCK,)).fetchone()['acquired']:
            return {**result, 'status': 'busy'}
        try:
            rows = conn.execute(
                """SELECT id,sbp_check_failures,sbp_check_revision FROM payment_attempts
                   WHERE provider='sbp' AND status='pending' AND provider_payment_id IS NOT NULL
                     AND NOT sbp_requires_review AND sbp_next_check_at<=now()
                   ORDER BY sbp_next_check_at,id LIMIT %s""", (limit,)).fetchall()
            if not apply:
                return {**result, 'status': 'dry_run', 'due_in_batch': len(rows)}
            for row in rows:
                # Автокоммит: после аварии попытка снова доступна; банк не вызывается POST.
                claimed = conn.execute("""UPDATE payment_attempts
                                SET sbp_next_check_at=now()+interval '60 seconds',
                                    sbp_check_revision=sbp_check_revision+1
                                WHERE id=%s AND status='pending' AND sbp_check_revision=%s
                                RETURNING sbp_check_revision""",
                                (row['id'], row['sbp_check_revision'])).fetchone()
                if claimed is None:
                    result['superseded'] += 1
                    continue
                failures = 0
                review = False
                delay = 60
                try:
                    state = verify_background(dsn, row['id'])['status']
                    code = 'pending' if state == 'pending' else 'settled'
                except (SBPProviderError, psycopg.Error):
                    failures = min(row['sbp_check_failures'] + 1, 1000)
                    delay = retry_seconds(failures)
                    code = 'retry'
                except (HTTPException, SBPReconcileError, SBPVerificationError):
                    # Не подтверждаем спорные суммы/магазин/режим; никакого текста ответа в логах.
                    review = True
                    code = 'review'
                finished = conn.execute("""UPDATE payment_attempts SET sbp_next_check_at=now()+(%s * interval '1 second'),
                                sbp_check_failures=%s,sbp_check_result=%s,sbp_requires_review=%s
                                WHERE id=%s AND sbp_check_revision=%s RETURNING id""",
                             (delay, failures, code, review, row['id'], claimed['sbp_check_revision'])).fetchone()
                result['checked'] += 1
                result[code if finished else 'superseded'] += 1
            return {**result, 'status': 'processed'}
        finally:
            conn.execute('SELECT pg_advisory_unlock(%s)', (_LOCK,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='один пакет вместо службы')
    parser.add_argument('--apply', action='store_true', help='применять подтверждённые результаты')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit: 1..100')
    if not args.once and not args.apply:
        parser.error('служба требует --apply; просмотр: --once')
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    while True:
        try:
            result = service_once(os.environ['DATABASE_URL'], apply=args.apply, limit=args.limit)
        except psycopg.Error:
            result = {'status': 'database_unavailable'}
        if args.apply:
            try:
                from .auto_schedule import service_once as schedule_once
                result['slots'] = schedule_once(os.environ['DATABASE_URL'])
            except psycopg.Error:
                result['slots'] = {'status': 'retry'}
        print(json.dumps(result), flush=True)
        if args.once:
            return
        if stopped.wait(60):
            return


if __name__ == '__main__':
    main()
