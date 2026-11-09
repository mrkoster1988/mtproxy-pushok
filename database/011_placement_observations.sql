-- v0.037 — подтверждения оператора: журнал, а не автоматическая проверка Telegram.
-- Результат оператора может быть отрицательным; записи не перезаписываем.
CREATE TABLE IF NOT EXISTS placement_observations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
    observer_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    result TEXT NOT NULL CHECK (result IN ('visible', 'not_visible')),
    method TEXT NOT NULL CHECK (method = 'separate_unsubscribed_client'),
    observed_at TIMESTAMPTZ NOT NULL,
    note TEXT NOT NULL CHECK (length(btrim(note)) BETWEEN 12 AND 2000),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS placement_observations_campaign_order_idx
    ON placement_observations (campaign_id, id DESC);
