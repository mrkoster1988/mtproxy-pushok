-- Все способы подачи кампании создают уведомление в одной транзакции.
CREATE OR REPLACE FUNCTION enqueue_campaign_moderation_notice()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.moderation_status <> 'pending' OR NEW.archived_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.moderation_status = 'pending' THEN
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO bot_operator_notice_outbox (campaign_id, operator_user_id)
    SELECT NEW.id, s.operator_user_id
    FROM bot_operator_subscriptions s
    JOIN users u ON u.id = s.operator_user_id
    WHERE u.role IN ('admin', 'operator') AND u.telegram_user_id > 0
    ON CONFLICT (campaign_id, operator_user_id) DO UPDATE
    SET delivered_at = NULL, last_attempt_at = NULL, attempts = 0, created_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS campaign_moderation_notice ON campaigns;
CREATE TRIGGER campaign_moderation_notice
AFTER INSERT OR UPDATE OF moderation_status ON campaigns
FOR EACH ROW EXECUTE FUNCTION enqueue_campaign_moderation_notice();
