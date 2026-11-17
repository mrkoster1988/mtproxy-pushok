-- Начало статистики отделено от начала призовой программы.
ALTER TABLE hall_settings ADD COLUMN IF NOT EXISTS rewards_started_at TIMESTAMPTZ;
UPDATE hall_settings SET rewards_started_at=started_at WHERE rewards_started_at IS NULL;
ALTER TABLE hall_settings ALTER COLUMN rewards_started_at SET NOT NULL;
ALTER TABLE hall_settings ALTER COLUMN rewards_started_at SET DEFAULT now();
UPDATE hall_settings SET started_at=coalesce((SELECT min(created_at) FROM users),started_at);
CREATE INDEX IF NOT EXISTS users_created_at_hall ON users(created_at);
CREATE TABLE IF NOT EXISTS hall_history_cursor (
 kind TEXT PRIMARY KEY CHECK(kind IN ('week','month','year')),
 next_start TIMESTAMPTZ NOT NULL
);
