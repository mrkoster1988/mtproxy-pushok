-- Долговечная блокировка узла на время внешнего изменения в Telegram.
-- Не освобождается по таймауту: бот мог принять изменение до обрыва связи.
ALTER TABLE channel_promotion_bindings ALTER COLUMN confirmed_by DROP NOT NULL;
CREATE TABLE IF NOT EXISTS telegram_binding_jobs (
 proxy_node_id UUID PRIMARY KEY REFERENCES proxy_nodes(id),
 id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 campaign_id UUID UNIQUE REFERENCES campaigns(id),
 channel_id UUID NOT NULL REFERENCES sponsor_channels(id),
 channel_url TEXT NOT NULL,
 node_host TEXT NOT NULL,
 node_port INTEGER NOT NULL,
 phase TEXT NOT NULL DEFAULT 'inspect' CHECK(phase IN ('inspect','bind','propagating','done')),
 ad_tag TEXT UNIQUE CHECK(ad_tag ~ '^[0-9a-f]{32}$'),
 owner_telegram_id BIGINT,
 telegram_channel_id BIGINT,
 bot_message_id BIGINT,
 retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 verified_at TIMESTAMPTZ,
 error_code TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Отдельный системный журнал не меняет ограничения старых, повторяемых миграций аудита.
CREATE TABLE IF NOT EXISTS telegram_binding_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 job_id UUID NOT NULL UNIQUE,
 proxy_node_id UUID NOT NULL REFERENCES proxy_nodes(id),
 campaign_id UUID REFERENCES campaigns(id),
 channel_id UUID NOT NULL REFERENCES sponsor_channels(id),
 owner_telegram_id BIGINT NOT NULL,
 telegram_channel_id BIGINT NOT NULL,
 bot_message_id BIGINT NOT NULL,
 verified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
