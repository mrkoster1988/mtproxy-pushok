-- v0.022. Реестр тегов, полученных оператором вручную через @MTProxyBot.
-- Не изменяет конфигурации существующих прокси, их трафик или кампании.
CREATE TABLE IF NOT EXISTS proxy_sponsor_registrations (
    proxy_node_id UUID PRIMARY KEY REFERENCES proxy_nodes(id) ON DELETE CASCADE,
    engine TEXT NOT NULL CHECK (engine IN ('telemt', 'official_mtproxy')),
    ad_tag TEXT NOT NULL
        CHECK (ad_tag ~ '^[0-9a-f]{32}$' AND ad_tag <> repeat('0', 32)),
    registered_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ad_tag)
);
