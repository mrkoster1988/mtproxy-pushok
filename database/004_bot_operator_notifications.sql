-- Уведомления только администратору, инициированные очередью pending-заявок.
-- Подтверждение после sendMessage: минимум одно уведомление, возможен повтор
-- после сбоя между Telegram sendMessage и сохранением delivered_at.
CREATE TABLE IF NOT EXISTS bot_operator_notifications (
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    operator_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, operator_user_id)
);
