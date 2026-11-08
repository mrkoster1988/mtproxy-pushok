-- Надёжная очередь операторских уведомлений.
-- Записи создаются в той же транзакции, что и submit заявки.
-- Повторные попытки допускаются; доставка "at least once", не "exactly once".
CREATE TABLE IF NOT EXISTS bot_operator_notice_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    operator_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    delivered_at TIMESTAMPTZ,
    UNIQUE (campaign_id, operator_user_id)
);

CREATE INDEX IF NOT EXISTS bot_operator_notice_pending_idx
    ON bot_operator_notice_outbox (created_at, id)
    WHERE delivered_at IS NULL;
