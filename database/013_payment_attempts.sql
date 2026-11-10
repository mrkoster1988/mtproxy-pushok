-- v0.047 — платежные попытки Stars / СБП без подключения реальных провайдеров.
-- Этот файл не подтверждает оплату и не изменяет статус существующих кампаний.
-- Применять только после резервного копирования и оценки текущей БД.
CREATE TABLE IF NOT EXISTS payment_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK (provider IN ('telegram_stars', 'sbp')),
    payment_channel TEXT NOT NULL CHECK (payment_channel IN ('telegram_bot', 'external_web')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    currency CHAR(3) NOT NULL CHECK (currency IN ('XTR', 'RUB')),
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'pending', 'succeeded', 'failed', 'refunded')),
    -- Ключ, созданный сервером; не использовать пользовательский payload как доказательство оплаты.
    request_key UUID NOT NULL DEFAULT gen_random_uuid(),
    provider_payment_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at TIMESTAMPTZ,
    CHECK (
        (provider = 'telegram_stars' AND payment_channel = 'telegram_bot' AND currency = 'XTR')
        OR (provider = 'sbp' AND payment_channel = 'external_web' AND currency = 'RUB')
    ),
    CHECK ((status NOT IN ('succeeded', 'refunded')) OR provider_payment_id IS NOT NULL),
    UNIQUE (provider, request_key),
    UNIQUE (provider, provider_payment_id)
);

-- DB-гарантия не позволит отметить две платежные попытки как успешно
-- оплаченные для одной кампании. Реальные двойные списания нужно отдельно
-- обрабатывать у провайдера возвратом, а не молча терять.
CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_one_success_per_campaign
    ON payment_attempts (campaign_id)
    WHERE status = 'succeeded';

CREATE INDEX IF NOT EXISTS payment_attempts_campaign_idx
    ON payment_attempts (campaign_id, created_at DESC);
