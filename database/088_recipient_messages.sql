CREATE TABLE IF NOT EXISTS cabinet_telegram_receipts (
 telegram_id bigint NOT NULL, message_id bigint NOT NULL, sent_at timestamptz NOT NULL,
 body text NOT NULL, PRIMARY KEY(telegram_id,message_id)
);
CREATE INDEX IF NOT EXISTS cabinet_receipts_recipient_time ON cabinet_telegram_receipts(telegram_id,sent_at DESC);
CREATE TABLE IF NOT EXISTS cabinet_receipt_epoch (
 singleton boolean PRIMARY KEY CHECK(singleton), started timestamptz NOT NULL DEFAULT now()
);
INSERT INTO cabinet_receipt_epoch(singleton) VALUES(true) ON CONFLICT DO NOTHING;
