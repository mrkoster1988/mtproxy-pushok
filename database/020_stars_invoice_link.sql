-- Ссылка привязана к попытке, сумме и боту. Само создание ссылки не списывает Stars.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS stars_invoice_url TEXT;
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS stars_invoice_bot_id BIGINT;
