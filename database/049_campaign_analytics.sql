-- Только события отдельной ссылки. Это не счётчик показов спонсора MTProxy.
CREATE TABLE IF NOT EXISTS campaign_analytics (
 campaign_id UUID PRIMARY KEY REFERENCES campaigns(id),
 token TEXT NOT NULL UNIQUE CHECK(token ~ '^[a-f0-9]{32}$'),
 channel_id UUID NOT NULL REFERENCES sponsor_channels(id),
 channel_url TEXT NOT NULL,
 attribution_secret TEXT NOT NULL CHECK(length(attribution_secret)=64),
 chat_id BIGINT NOT NULL,
 bot_id BIGINT NOT NULL,
 test_environment BOOLEAN NOT NULL,
 invite_link TEXT NOT NULL UNIQUE,
 enabled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS campaign_link_visitors (
 campaign_id UUID NOT NULL REFERENCES campaign_analytics(campaign_id),
 visitor_hash TEXT NOT NULL,
 viewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 clicked_at TIMESTAMPTZ,
 PRIMARY KEY(campaign_id,visitor_hash)
);
CREATE TABLE IF NOT EXISTS campaign_link_joins (
 campaign_id UUID NOT NULL REFERENCES campaign_analytics(campaign_id),
 user_hash TEXT NOT NULL,
 joined_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(campaign_id,user_hash)
);
CREATE INDEX IF NOT EXISTS campaign_link_visitors_date ON campaign_link_visitors(campaign_id,viewed_at);
