-- Короткие публичные приглашения. Старые growth_profiles.code остаются действующими.
CREATE TABLE IF NOT EXISTS growth_referral_links (
    code TEXT PRIMARY KEY CHECK(code ~ '^[A-Z2-9]{8}$'),
    user_id UUID NOT NULL UNIQUE REFERENCES growth_profiles(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
