CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id BIGINT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'advertiser'
        CHECK (role IN ('advertiser', 'operator', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE proxy_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (host, port)
);

CREATE TABLE sponsor_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    telegram_url TEXT NOT NULL,
    telegram_channel_id BIGINT,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, telegram_url)
);

CREATE TABLE tariffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
    price_kopecks INTEGER NOT NULL CHECK (price_kopecks >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'RUB',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    advertiser_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    channel_id UUID NOT NULL REFERENCES sponsor_channels(id) ON DELETE RESTRICT,
    tariff_id UUID NOT NULL REFERENCES tariffs(id) ON DELETE RESTRICT,
    proxy_node_id UUID REFERENCES proxy_nodes(id) ON DELETE RESTRICT,
    tariff_name_snapshot TEXT NOT NULL,
    duration_seconds_snapshot INTEGER NOT NULL CHECK (duration_seconds_snapshot > 0),
    price_kopecks_snapshot INTEGER NOT NULL CHECK (price_kopecks_snapshot >= 0),
    currency_snapshot CHAR(3) NOT NULL,
    moderation_status TEXT NOT NULL DEFAULT 'draft'
        CHECK (moderation_status IN ('draft', 'pending', 'approved', 'rejected', 'changes_requested')),
    payment_status TEXT NOT NULL DEFAULT 'not_required'
        CHECK (payment_status IN ('not_required', 'awaiting_payment', 'proof_submitted', 'verified', 'rejected', 'refunded')),
    placement_status TEXT NOT NULL DEFAULT 'not_scheduled'
        CHECK (placement_status IN ('not_scheduled', 'scheduled', 'active', 'completed', 'cancelled')),
    requested_start_at TIMESTAMPTZ,
    scheduled_start_at TIMESTAMPTZ,
    scheduled_end_at TIMESTAMPTZ,
    moderator_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (scheduled_end_at IS NULL OR scheduled_start_at IS NOT NULL),
    CHECK (scheduled_end_at IS NULL OR scheduled_end_at > scheduled_start_at),
    CHECK (
        placement_status NOT IN ('scheduled', 'active')
        OR (proxy_node_id IS NOT NULL AND scheduled_start_at IS NOT NULL AND scheduled_end_at IS NOT NULL)
    )
);

ALTER TABLE campaigns
    ADD CONSTRAINT campaigns_proxy_schedule_no_overlap
    EXCLUDE USING gist (
        proxy_node_id WITH =,
        tstzrange(scheduled_start_at, scheduled_end_at, '[)') WITH &&
    )
    WHERE (placement_status IN ('scheduled', 'active'));

CREATE UNIQUE INDEX campaigns_one_active_per_node
    ON campaigns (proxy_node_id)
    WHERE placement_status = 'active';

CREATE INDEX campaigns_advertiser_created_idx
    ON campaigns (advertiser_user_id, created_at DESC);
CREATE INDEX campaigns_moderation_queue_idx
    ON campaigns (moderation_status, created_at);
CREATE INDEX campaigns_payment_queue_idx
    ON campaigns (payment_status, updated_at);

CREATE TABLE payment_proofs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
    file_storage_key TEXT NOT NULL,
    original_file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    submitted_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reviewed_by_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    review_comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX payment_proofs_campaign_created_idx
    ON payment_proofs (campaign_id, submitted_at DESC);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_campaign_created_idx
    ON audit_events (campaign_id, created_at DESC);

INSERT INTO tariffs (id, name, duration_seconds, price_kopecks, currency)
VALUES (
    '00000000-0000-4000-8000-000000000001',
    '1 день',
    86400,
    30000,
    'RUB'
)
ON CONFLICT (id) DO NOTHING;
