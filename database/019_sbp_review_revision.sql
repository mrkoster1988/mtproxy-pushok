-- Защита от записи устаревшего результата worker после ручного повтора.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_check_revision BIGINT NOT NULL DEFAULT 0;
