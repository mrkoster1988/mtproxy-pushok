ALTER TABLE vpn_scheduled_notices
    ADD COLUMN IF NOT EXISTS cabinet_sender boolean NOT NULL DEFAULT false;
