"""Stored outgoing notices for one cabinet account. The caller authorizes the subject."""
import logging

import psycopg
from psycopg.rows import dict_row

PAGE = 20

# Preserve older queue receipts where Telegram's original message body was not saved.
TELEGRAM = """
WITH recipient AS (SELECT id,telegram_user_id FROM users WHERE id=%s),
events AS (
 SELECT r.sent_at AS stamp, 'tg-'||r.message_id AS key, 'Telegram' AS title,r.body AS text,'sent' AS delivery
 FROM cabinet_telegram_receipts r JOIN recipient u ON u.telegram_user_id=r.telegram_id
 UNION ALL
 SELECT o.created_at, 'ad-'||o.id, 'Модерация кампании',
 CASE o.decision WHEN 'approved' THEN 'Заявка одобрена' WHEN 'rejected' THEN 'Заявка отклонена'
 ELSE 'Запрошены изменения' END || ' · ' || left(o.campaign_id::text,8),
 CASE WHEN o.delivered_at IS NOT NULL THEN 'sent' WHEN o.abandoned_at IS NOT NULL THEN 'failed' ELSE 'queued' END
 FROM bot_advertiser_notice_outbox o JOIN recipient u ON u.id=o.recipient_user_id
 WHERE o.delivered_at IS NULL OR o.delivered_at < (SELECT started FROM cabinet_receipt_epoch)
 UNION ALL
 SELECT o.created_at,'campaign-'||o.id,'Кампания',
 CASE o.event_type WHEN 'started' THEN 'Размещение началось' WHEN 'completed' THEN 'Размещение завершено'
 ELSE 'Сбой размещения' END, CASE WHEN o.delivered_at IS NOT NULL THEN 'sent' WHEN o.abandoned_at IS NOT NULL THEN 'failed' ELSE 'queued' END
 FROM campaign_notice_outbox o JOIN recipient u ON u.id=o.recipient_user_id
 WHERE o.delivered_at IS NULL OR o.delivered_at < (SELECT started FROM cabinet_receipt_epoch)
 UNION ALL
 SELECT o.created_at,'operator-'||o.id,'Кампания','Новая заявка на модерацию · '||left(o.campaign_id::text,8),
 CASE WHEN o.delivered_at IS NULL THEN 'queued' ELSE 'sent' END
 FROM bot_operator_notice_outbox o JOIN recipient u ON u.id=o.operator_user_id
 WHERE o.delivered_at IS NULL OR o.delivered_at < (SELECT started FROM cabinet_receipt_epoch)
 UNION ALL
 SELECT r.created_at,'gift-'||o.id,'День рождения','Поздравление с днём рождения',
 CASE WHEN o.delivered_at IS NOT NULL THEN 'sent' WHEN o.abandoned_at IS NOT NULL THEN 'failed' ELSE 'queued' END
 FROM birthday_notice_outbox o JOIN birthday_rewards r ON r.id=o.reward_id JOIN recipient u ON u.id=r.user_id
 WHERE o.delivered_at IS NULL OR o.delivered_at < (SELECT started FROM cabinet_receipt_epoch)
 UNION ALL
 SELECT to_timestamp(n.sent_at),'reminder-'||n.id,'Напоминание VPN',
 CASE n.kind WHEN 'never' THEN 'Первое подключение' WHEN 'idle' THEN 'Давно не подключались' ELSE 'Окончание подписки' END,'sent'
 FROM vpn_scheduled_notices n JOIN recipient u ON u.id=n.user_id
 WHERE to_timestamp(n.sent_at)<(SELECT started FROM cabinet_receipt_epoch)
)
SELECT extract(epoch from stamp)::bigint AS created,title,text,delivery FROM events
ORDER BY stamp DESC,key DESC LIMIT 21 OFFSET %s
"""

SUPPORT = """
SELECT extract(epoch from m.created_at)::bigint AS created,
 CASE m.sender WHEN 'staff' THEN 'Ответ сотрудника' ELSE 'Pushok ИИ' END AS title,m.body AS text,
 CASE WHEN o.delivered THEN 'sent' WHEN o.message_id IS NOT NULL THEN 'queued' ELSE 'in_app' END AS delivery
FROM users u JOIN support_conversations c ON c.telegram_id=u.telegram_user_id
JOIN support_chat_messages m ON m.conversation_id=c.id
LEFT JOIN support_bot_outbox o ON o.message_id=m.id AND o.telegram_id=u.telegram_user_id
WHERE u.id=%s AND m.sender IN ('staff','ai-agent')
ORDER BY m.created_at DESC,m.id DESC LIMIT 21 OFFSET %s
"""


def read(dsn, subject, source, offset):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute(SUPPORT if source=='support' else TELEGRAM,(subject,offset)).fetchall()
    return {'items':rows[:PAGE], 'next':offset+PAGE if len(rows)>PAGE else None}


def record(dsn, message):
    # Telegram has already accepted the message. A journal error must not send it twice.
    try:
        with psycopg.connect(dsn,connect_timeout=2) as conn:
            conn.execute("""INSERT INTO cabinet_telegram_receipts(telegram_id,message_id,sent_at,body)
                VALUES (%s,%s,to_timestamp(%s),%s) ON CONFLICT DO NOTHING""",
                (message['chat']['id'],message['message_id'],message['date'],message['text']))
    except (psycopg.Error,KeyError):
        logging.getLogger(__name__).warning('Could not store Telegram message receipt')
