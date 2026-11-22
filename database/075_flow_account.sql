-- Отдельный доступ Pushok Flow: существующий пользователь, только чтение профиля/кошелька.
-- Секрет устройства и токен доступа никогда не хранятся в открытом виде.
CREATE TABLE IF NOT EXISTS flow_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    scopes TEXT[] NOT NULL DEFAULT ARRAY['profile:read','wallet:read']::TEXT[]
        CHECK (scopes = ARRAY['profile:read','wallet:read']::TEXT[]),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at AND expires_at <= created_at + INTERVAL '12 hours')
);
CREATE INDEX IF NOT EXISTS flow_sessions_user_expiry ON flow_sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS flow_sessions_expiry ON flow_sessions(expires_at);

CREATE TABLE IF NOT EXISTS flow_device_requests (
    id UUID PRIMARY KEY,
    device_code_hash CHAR(64) NOT NULL UNIQUE CHECK (device_code_hash ~ '^[0-9a-f]{64}$'),
    user_code CHAR(12) NOT NULL UNIQUE CHECK (user_code ~ '^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12}$'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','approved','denied')),
    decided_by UUID REFERENCES users(id) ON DELETE CASCADE,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_polled_at TIMESTAMPTZ,
    -- Удаление отозванной сессии удаляет и grant: повторная выдача невозможна.
    session_id UUID UNIQUE REFERENCES flow_sessions(id) ON DELETE CASCADE,
    CHECK (expires_at > created_at AND expires_at <= created_at + INTERVAL '5 minutes'),
    CHECK ((state = 'pending' AND decided_by IS NULL AND decided_at IS NULL)
        OR (state <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    CHECK (session_id IS NULL OR state = 'approved')
);
CREATE INDEX IF NOT EXISTS flow_device_requests_expiry ON flow_device_requests(expires_at);

-- Общие для всех API workers минутные лимиты; client_key — хеш адреса сокета.
CREATE TABLE IF NOT EXISTS flow_rate_windows (
    bucket TEXT NOT NULL CHECK (bucket IN ('device','token','consent','profile','logout')),
    client_key VARCHAR(64) NOT NULL,
    window_started TIMESTAMPTZ NOT NULL,
    requests INTEGER NOT NULL CHECK (requests > 0),
    PRIMARY KEY (bucket, client_key)
);
CREATE INDEX IF NOT EXISTS flow_rate_windows_expiry ON flow_rate_windows(window_started);
