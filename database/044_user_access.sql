-- Блокировка закрывает вход, не удаляя аккаунт, баланс и оплаченные заявки.
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ;
