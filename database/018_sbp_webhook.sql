-- Только первый сигнал для сверки; callback не является доказательством оплаты.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS sbp_webhook_received_at TIMESTAMPTZ;
