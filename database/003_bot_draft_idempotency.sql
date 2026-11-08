-- Привязка черновика к устойчивому ключу запроса Telegram-бота.
-- Существующие кампании сохраняются: столбец допускает NULL.
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS bot_request_key UUID;

CREATE UNIQUE INDEX IF NOT EXISTS campaigns_bot_request_key_uq
    ON campaigns (bot_request_key)
    WHERE bot_request_key IS NOT NULL;
