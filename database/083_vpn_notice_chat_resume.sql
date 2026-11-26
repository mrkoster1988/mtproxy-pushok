ALTER TABLE vpn_scheduled_notices
    ADD COLUMN IF NOT EXISTS chat_reopened boolean NOT NULL DEFAULT false;
