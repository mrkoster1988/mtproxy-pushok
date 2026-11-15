-- Общий канал для свободного времени. Рекламные кампании имеют приоритет.
CREATE TABLE IF NOT EXISTS default_sponsor_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
 channel_id UUID REFERENCES sponsor_channels(id) ON DELETE SET NULL,
 updated_by UUID REFERENCES users(id),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO default_sponsor_settings(singleton) VALUES(TRUE) ON CONFLICT DO NOTHING;
