-- Один ключ браузерной отправки создаёт ровно одну заявку, включая повтор после потери ответа.
CREATE TABLE IF NOT EXISTS order_submissions (
    user_id UUID NOT NULL REFERENCES users(id),
    request_id UUID NOT NULL,
    request_hash TEXT NOT NULL,
    campaign_id UUID NOT NULL REFERENCES campaigns(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, request_id)
);
