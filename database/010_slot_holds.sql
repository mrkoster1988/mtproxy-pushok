-- v0.034: удержание рекламного слота на 15 минут до оплаты.
-- Чистка просроченных hold выполняется под блокировкой узла в API.
CREATE TABLE IF NOT EXISTS slot_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    proxy_node_id UUID NOT NULL REFERENCES proxy_nodes(id) ON DELETE RESTRICT,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT slot_holds_positive_interval CHECK (ends_at > starts_at),
    CONSTRAINT slot_holds_positive_expiry CHECK (expires_at > created_at),
    CONSTRAINT slot_holds_one_per_campaign UNIQUE (campaign_id),
    CONSTRAINT slot_holds_no_overlap EXCLUDE USING gist (
        proxy_node_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&
    )
);
CREATE INDEX IF NOT EXISTS slot_holds_expiry_idx ON slot_holds (expires_at);
