-- Снимки параметров СБП нужны для точного повторения запроса после тайм-аута.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_confirmation_url TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_return_url TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_shop_id TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_test_mode BOOLEAN;
CREATE INDEX IF NOT EXISTS payment_attempts_open_sbp
    ON payment_attempts(campaign_id, created_at DESC)
    WHERE provider='sbp' AND status IN ('created','pending');
