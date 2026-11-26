CREATE TABLE IF NOT EXISTS support_language_preferences (
    telegram_id bigint PRIMARY KEY CHECK (telegram_id > 0),
    preference text NOT NULL CHECK (preference IN ('auto','ru','en','es'))
);
CREATE INDEX IF NOT EXISTS support_messages_thread ON support_chat_messages(conversation_id,id);
ALTER TABLE support_conversations ADD COLUMN IF NOT EXISTS reply_locale text NOT NULL DEFAULT 'ru';
