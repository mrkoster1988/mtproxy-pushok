-- Служебный аккаунт не является Telegram-пользователем и не может войти в кабинет.
CREATE TABLE IF NOT EXISTS service_accounts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO service_accounts(id,name,kind) VALUES ('ai-agent','Агент ИИ','moderation') ON CONFLICT(id) DO NOTHING;
ALTER TABLE ai_moderation_events ADD COLUMN IF NOT EXISTS actor_id TEXT REFERENCES service_accounts(id) DEFAULT 'ai-agent';
ALTER TABLE ai_moderation_events ADD COLUMN IF NOT EXISTS topic TEXT;
-- Для старых событий тема на момент решения неизвестна: не подменяем её текущей.
CREATE INDEX IF NOT EXISTS ai_moderation_events_actor_time ON ai_moderation_events(actor_id,created_at DESC);
