-- v0.049. Счета Telegram Stars, ручная сверка неподтвержденных событий.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS payer_telegram_user_id BIGINT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS precheckout_query_id TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS precheckout_approved_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_precheckout_unique
    ON payment_attempts(precheckout_query_id) WHERE precheckout_query_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS stars_payment_receipts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id UUID REFERENCES payment_attempts(id) ON DELETE RESTRICT,
    telegram_payment_charge_id TEXT NOT NULL UNIQUE,
    payer_telegram_user_id BIGINT NOT NULL,
    currency CHAR(3) NOT NULL,
    total_amount INTEGER NOT NULL CHECK(total_amount > 0),
    handling_status TEXT NOT NULL DEFAULT 'received'
        CHECK (handling_status IN ('received', 'applied', 'requires_review')),
    reason_code TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS stars_receipts_review_idx
  ON stars_payment_receipts(received_at)
  WHERE handling_status='requires_review';
