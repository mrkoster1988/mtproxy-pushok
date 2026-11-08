-- Операторы добровольно включают сообщения о новых заявках командой /moderation.
-- Сохраняем только ссылку на пользователя кабинета; перед отправкой заново проверяем роль.
CREATE TABLE IF NOT EXISTS bot_operator_subscriptions (
    operator_user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
