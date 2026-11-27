ALTER TABLE birthday_settings ADD COLUMN IF NOT EXISTS verification_enabled BOOLEAN NOT NULL DEFAULT true;
