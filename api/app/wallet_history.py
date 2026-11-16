"""История кабинета: проводки кошелька и прямые оплаты, без двойного списания."""


def history(conn, uid, page, limit=30):
    # Отдельный источник direct никогда не изменяет остатки кошелька.
    rows = conn.execute('''WITH events AS (
        SELECT e.created_at,e.entry_number AS sort_number,e.id::text AS sort_id,
          to_jsonb(e) - 'operation_key' - 'user_id' - 'actor_user_id' || jsonb_build_object(
            'source','wallet','archived_at',c.archived_at,'automatic',EXISTS(SELECT 1 FROM wallet_payments p WHERE p.campaign_id=e.campaign_id AND p.automatic),
            'reversed',EXISTS(SELECT 1 FROM wallet_entries r WHERE r.reversal_of=e.id),
            'actor_username',u.username,'actor_telegram_id',u.telegram_user_id) AS item
        FROM wallet_entries e LEFT JOIN users u ON u.id=e.actor_user_id LEFT JOIN campaigns c ON c.id=e.campaign_id WHERE e.user_id=%(uid)s
        UNION ALL
        SELECT coalesce(a.settled_at,a.created_at),0,'direct:'||a.id::text,
          jsonb_build_object('id','direct:'||a.id::text,'source','direct','kind','direct_payment',
            'archived_at',c.archived_at,'currency',a.currency,'delta',-a.amount,'balance_after',NULL,'entry_number',NULL,
            'created_at',coalesce(a.settled_at,a.created_at),'campaign_id',a.campaign_id,
            'reason',ch.title,'provider',a.provider,'payment_status',a.status,
            'automatic',false,'reversed',false)
        FROM payment_attempts a JOIN campaigns c ON c.id=a.campaign_id
          JOIN sponsor_channels ch ON ch.id=c.channel_id
        WHERE c.advertiser_user_id=%(uid)s AND a.status IN ('succeeded','refunded')
          AND ((a.provider='telegram_stars' AND NOT a.telegram_test_environment) OR (a.provider='sbp' AND a.sbp_test_mode IS FALSE))
          AND NOT EXISTS(SELECT 1 FROM wallet_payments p WHERE p.campaign_id=c.id)
    ), selected AS (
      SELECT * FROM events ORDER BY created_at DESC,sort_number DESC,sort_id DESC LIMIT %(limit)s OFFSET %(offset)s
    ) SELECT (SELECT count(*) FROM events) AS total,
        coalesce(jsonb_agg(item ORDER BY created_at DESC,sort_number DESC,sort_id DESC) FILTER(WHERE item IS NOT NULL),'[]'::jsonb) AS entries
      FROM selected''', {'uid':uid,'limit':limit,'offset':page*limit}).fetchone()
    return dict(entries=rows['entries'],total=rows['total'],page=page,page_size=limit)
