-- Состояние фоновой сверки отдельно от результата платежа.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_next_check_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_check_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_check_result TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_requires_review BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS payment_attempts_sbp_due
    ON payment_attempts(sbp_next_check_at, id)
    WHERE provider='sbp' AND status='pending' AND NOT sbp_requires_review;
