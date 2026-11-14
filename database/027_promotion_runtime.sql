-- Подготовленные в Telegram связи канала и тега. Секреты не возвращаются списком.
CREATE TABLE IF NOT EXISTS channel_promotion_bindings (
 proxy_node_id UUID NOT NULL REFERENCES proxy_nodes(id),
 channel_id UUID NOT NULL REFERENCES sponsor_channels(id),
 channel_url TEXT NOT NULL,
 node_host TEXT NOT NULL,
 node_port INTEGER NOT NULL,
 ad_tag TEXT NOT NULL CHECK(ad_tag ~ '^[0-9a-f]{32}$'),
 confirmed_by UUID NOT NULL REFERENCES users(id),
 confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(proxy_node_id,channel_id), UNIQUE(ad_tag)
);
CREATE TABLE IF NOT EXISTS promotion_runtime (
 proxy_node_id UUID PRIMARY KEY REFERENCES proxy_nodes(id),
 campaign_id UUID REFERENCES campaigns(id),
 state TEXT NOT NULL CHECK(state IN ('applied','idle')),
 checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
