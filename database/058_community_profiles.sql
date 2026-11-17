ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE verified_profiles ADD COLUMN IF NOT EXISTS birthday_locked BOOLEAN NOT NULL DEFAULT false;
UPDATE verified_profiles SET birthday_locked=true WHERE birth_day IS NOT NULL;
CREATE TABLE IF NOT EXISTS ai_moderation_checks (
 campaign_id UUID PRIMARY KEY REFERENCES campaigns(id), snapshot TIMESTAMPTZ NOT NULL,
 revision TEXT NOT NULL, attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decision TEXT NOT NULL DEFAULT 'retry', reason TEXT NOT NULL DEFAULT ''
);

ALTER TABLE verified_profiles ADD COLUMN IF NOT EXISTS transferred_to UUID REFERENCES users(id);
-- Системный журнал отдельно от человеческих решений: не приписываем действие сотруднику.
CREATE TABLE IF NOT EXISTS ai_moderation_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 campaign_id UUID NOT NULL REFERENCES campaigns(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decision TEXT NOT NULL, reason TEXT NOT NULL, revision TEXT NOT NULL
);
