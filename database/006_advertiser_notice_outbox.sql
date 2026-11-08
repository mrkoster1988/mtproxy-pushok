-- v0.020: долговечные уведомления РЕКЛАМОДАТЕЛЮ о решениях модератора.
-- Триггер срабатывает на любой путь модерации: веб-кабинет и Telegram-бот.
-- Запись создаётся в той же транзакции, что и изменение кампании.
CREATE TABLE IF NOT EXISTS bot_advertiser_notice_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    decision TEXT NOT NULL
        CHECK (decision IN ('approved', 'rejected', 'changes_requested')),
    comment_snapshot TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claim_token UUID,
    lease_until TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    abandoned_at TIMESTAMPTZ,
    CHECK (NOT (delivered_at IS NOT NULL AND abandoned_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS bot_advertiser_notice_due_idx
    ON bot_advertiser_notice_outbox (next_attempt_at, created_at, id)
    WHERE delivered_at IS NULL AND abandoned_at IS NULL;

CREATE OR REPLACE FUNCTION pushok_enqueue_moderation_notice()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.moderation_status IS DISTINCT FROM OLD.moderation_status
       AND NEW.moderation_status IN ('approved', 'rejected', 'changes_requested') THEN
        INSERT INTO bot_advertiser_notice_outbox
            (campaign_id, recipient_user_id, decision, comment_snapshot)
        VALUES (
            NEW.id, NEW.advertiser_user_id, NEW.moderation_status,
            LEFT(NEW.moderator_comment, 2000)
        );
    END IF;
    RETURN NEW;
END;
$$;

-- Идемпотентно для повторного запуска мастера на уже существующей БД.
DROP TRIGGER IF EXISTS campaigns_advertiser_notice_enqueue ON campaigns;
CREATE TRIGGER campaigns_advertiser_notice_enqueue
AFTER UPDATE OF moderation_status ON campaigns
FOR EACH ROW EXECUTE FUNCTION pushok_enqueue_moderation_notice();
