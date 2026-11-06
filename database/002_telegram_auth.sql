ALTER TABLE users
    ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_name TEXT,
    ADD COLUMN IF NOT EXISTS username TEXT;

CREATE TABLE IF NOT EXISTS telegram_login_replays (
    telegram_hash CHAR(64) PRIMARY KEY,
    auth_date TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_sessions_user_expiry_idx
    ON user_sessions (user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS telegram_login_replays_consumed_idx
    ON telegram_login_replays (consumed_at);
