-- Проверяемое обоснование решения ИИ и автор конкретного уведомления.
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'none';
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]';
ALTER TABLE ai_moderation_events ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_moderation_events ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'none';
ALTER TABLE ai_moderation_events ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]';
ALTER TABLE bot_advertiser_notice_outbox ADD COLUMN IF NOT EXISTS ai_moderation_event_id UUID
    REFERENCES ai_moderation_events(id);
