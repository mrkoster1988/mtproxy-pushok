-- v0.038 — приватные скриншоты к операторским наблюдениям.
-- Одно изображение на одну запись; содержимое не публикуется напрямую.
CREATE TABLE IF NOT EXISTS placement_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id BIGINT NOT NULL UNIQUE REFERENCES placement_observations(id) ON DELETE RESTRICT,
    uploader_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    storage_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
