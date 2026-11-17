-- Общий вход и добровольная тема подключения. Переписка и подписки не собираются.
CREATE TABLE IF NOT EXISTS proxy_gateway_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled BOOLEAN NOT NULL DEFAULT false,
 port INTEGER NOT NULL DEFAULT 10443 CHECK(port BETWEEN 1024 AND 65000),
 interests_enabled BOOLEAN NOT NULL DEFAULT false,
 revision UUID NOT NULL DEFAULT gen_random_uuid(),
 updated_by UUID REFERENCES users(id), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO proxy_gateway_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS user_proxy_interests (
 user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 topic TEXT NOT NULL, consent_version TEXT NOT NULL DEFAULT 'proxy-interests-v1',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS proxy_gateway_changes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), actor_id UUID NOT NULL REFERENCES users(id),
 settings JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
