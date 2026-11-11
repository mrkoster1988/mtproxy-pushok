-- Старые счета относятся к основной среде. Режим фиксируется до запроса Telegram.
ALTER TABLE payment_attempts ADD COLUMN IF NOT EXISTS telegram_test_environment BOOLEAN NOT NULL DEFAULT false;
