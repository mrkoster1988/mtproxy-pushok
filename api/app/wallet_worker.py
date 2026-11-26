"""Пакетная сверка пополнений и явно разрешённых автоматических списаний."""
from types import SimpleNamespace
import psycopg
from fastapi import HTTPException
from . import wallet_topups as topups, wallet_ledger as ledger, wallet_controls as controls
from .sbp_provider import SBPProviderError
from .sbp_verification import SBPVerificationError


def service_once(dsn,limit=20):
    report={'topups_checked':0,'auto_paid':0,'deferred':0}
    with topups.connect(dsn) as conn:
        # У новых оплат свой бюджет: старые проверки возвратов не занимают их очередь.
        pending=conn.execute("SELECT id,user_id FROM wallet_topups WHERE provider IN ('sbp','platega','antilopay') AND status='pending' AND next_check_at<=now() ORDER BY next_check_at,id LIMIT %s",(limit,)).fetchall()
        history=conn.execute("SELECT id,user_id FROM wallet_topups WHERE provider IN ('sbp','platega','antilopay') AND status='succeeded' AND next_check_at<=now() ORDER BY next_check_at,id LIMIT %s",(max(1,limit//4),)).fetchall()
        rows=pending+history
    for row in rows:
        try:
            topups.verify(dsn,row['id'],row['user_id']);report['topups_checked']+=1
        except (HTTPException,SBPProviderError,SBPVerificationError,psycopg.Error):
            report['deferred']+=1
            with topups.connect(dsn) as conn:
                conn.execute("UPDATE wallet_topups SET check_failures=check_failures+1,review_reason='verification_retry',next_check_at=now()+interval '5 minutes' WHERE id=%s",(row['id'],))
    with topups.connect(dsn) as conn:
        rows=conn.execute('''SELECT r.* FROM wallet_star_refunds r JOIN stars_payment_receipts s ON s.telegram_payment_charge_id=r.charge_id
            WHERE r.status='received' AND s.wallet_topup_id IS NOT NULL ORDER BY r.created_at LIMIT %s''',(limit,)).fetchall()
    for row in rows:
        try:topups.refund_stars(dsn,SimpleNamespace(telegram_user_id=row['telegram_user_id'],currency=row['currency'],total_amount=row['amount'],telegram_payment_charge_id=row['charge_id']))
        except (HTTPException,psycopg.Error):report['deferred']+=1
    with topups.connect(dsn) as conn:
        if not controls.settings(conn)['enabled']:return report
        # Все подходящие кампании, без голодания из-за первых заявок без средств.
        rows=conn.execute('''SELECT c.id,c.advertiser_user_id,p.currency FROM campaigns c JOIN wallet_preferences p ON p.user_id=c.advertiser_user_id
            JOIN tariffs t ON t.id=c.tariff_id WHERE p.auto_pay AND p.currency='RUB' AND c.wallet_checked_at<now()-interval '1 minute' AND c.archived_at IS NULL AND c.moderation_status='approved'
            AND c.payment_status='awaiting_payment' AND c.placement_status='not_scheduled' AND c.price_kopecks_snapshot>0
            AND (p.currency='XTR' OR c.purchase_surface<>'telegram')
            ORDER BY c.wallet_checked_at,c.id LIMIT %s''',(limit,)).fetchall()
    for row in rows:
        if report['auto_paid']>=limit:break
        try:
            with topups.connect(dsn) as conn:
                ledger.charge(conn,row['id'],row['advertiser_user_id'],row['currency'],automatic=True)
            report['auto_paid']+=1
        except (HTTPException,psycopg.Error):report['deferred']+=1
        finally:
            with topups.connect(dsn) as conn:
                conn.execute('UPDATE campaigns SET wallet_checked_at=now() WHERE id=%s',(row['id'],))
    return report
