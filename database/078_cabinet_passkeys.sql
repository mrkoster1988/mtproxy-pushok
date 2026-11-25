CREATE TABLE IF NOT EXISTS cabinet_passkeys (
    credential_id BYTEA PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cabinet_passkeys_user ON cabinet_passkeys(user_id);

CREATE TABLE IF NOT EXISTS cabinet_used_challenges (
    digest BYTEA PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS cabinet_used_challenges_expiry ON cabinet_used_challenges(expires_at);
