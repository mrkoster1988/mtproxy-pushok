CREATE TABLE IF NOT EXISTS support_telegram_topics (
    case_id bigint PRIMARY KEY REFERENCES support_conversations(id) ON DELETE CASCADE,
    bot_id bigint NOT NULL, chat_id bigint NOT NULL, thread_id bigint NOT NULL,
    UNIQUE (bot_id,chat_id,thread_id)
);
CREATE TABLE IF NOT EXISTS subscription_widgets (
    telegram_id bigint PRIMARY KEY, message_id bigint, format text NOT NULL DEFAULT 'widget',
    snapshot text, due_at timestamptz NOT NULL DEFAULT now(), suspended boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS pushok_editorial_drafts (
    id uuid PRIMARY KEY, product text NOT NULL, variants jsonb NOT NULL, art text NOT NULL,
    revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
    published_id bigint, channel text, state text NOT NULL DEFAULT 'draft', locale text
);
ALTER TABLE pushok_editorial_drafts ADD COLUMN IF NOT EXISTS illustration bytea;
CREATE TABLE IF NOT EXISTS notification_write_grants (
    telegram_id bigint PRIMARY KEY, retry_at timestamptz, seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notification_write_grants_due ON notification_write_grants(retry_at) WHERE retry_at IS NOT NULL;
