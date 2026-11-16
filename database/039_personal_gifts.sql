-- Персональный код привязан к подтверждённому Telegram ID, не к username.
ALTER TABLE wallet_gifts ADD COLUMN IF NOT EXISTS recipient_telegram_id BIGINT;
ALTER TABLE wallet_gifts DROP CONSTRAINT IF EXISTS wallet_gifts_recipient_check;
ALTER TABLE wallet_gifts ADD CONSTRAINT wallet_gifts_recipient_check CHECK(
 recipient_telegram_id IS NULL OR
 (recipient_telegram_id>0 AND recipient_telegram_id<4503599627370496 AND max_uses=1)
);
