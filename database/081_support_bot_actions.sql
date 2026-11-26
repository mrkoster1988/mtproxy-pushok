ALTER TABLE support_bot_contacts
    ADD COLUMN IF NOT EXISTS pending_action text CHECK (pending_action IN ('question','idea','human','problem')),
    ADD COLUMN IF NOT EXISTS bot_id bigint NOT NULL DEFAULT 0;
ALTER TABLE support_bot_outbox ADD COLUMN IF NOT EXISTS bot_id bigint NOT NULL DEFAULT 0;
