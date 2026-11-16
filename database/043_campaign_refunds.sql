ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS refunded_amount BIGINT NOT NULL DEFAULT 0 CHECK(refunded_amount>=0);
ALTER TABLE wallet_payments ADD COLUMN IF NOT EXISTS refunded_cash BIGINT NOT NULL DEFAULT 0 CHECK(refunded_cash>=0 AND refunded_cash<=cash_amount);
ALTER TABLE wallet_payments ADD COLUMN IF NOT EXISTS refunded_bonus BIGINT NOT NULL DEFAULT 0 CHECK(refunded_bonus>=0 AND refunded_bonus<=bonus_amount);
UPDATE wallet_payments SET refunded_cash=cash_amount,refunded_bonus=bonus_amount WHERE status='refunded';
UPDATE campaigns c SET refunded_amount=p.cash_amount+p.bonus_amount FROM wallet_payments p WHERE p.campaign_id=c.id AND p.status='refunded' AND c.refunded_amount=0;
UPDATE campaigns c SET refunded_amount=a.amount FROM payment_attempts a WHERE a.campaign_id=c.id AND a.status='refunded' AND c.refunded_amount=0;
UPDATE campaigns c SET refunded_amount=price_kopecks_snapshot WHERE payment_status='refunded' AND refunded_amount=0 AND NOT EXISTS(SELECT 1 FROM payment_attempts a WHERE a.campaign_id=c.id) AND NOT EXISTS(SELECT 1 FROM wallet_payments w WHERE w.campaign_id=c.id);
CREATE TABLE IF NOT EXISTS campaign_refunds (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),request_id UUID NOT NULL UNIQUE,
 campaign_id UUID NOT NULL REFERENCES campaigns(id),actor_id UUID NOT NULL REFERENCES users(id),
 source TEXT NOT NULL CHECK(source IN ('wallet','sbp','telegram_stars','manual')),
 currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR')),
 amount BIGINT NOT NULL CHECK(amount>0),reason TEXT NOT NULL CHECK(length(reason) BETWEEN 3 AND 500),
 status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','pending','succeeded','canceled','review')),
 provider_refund_id TEXT UNIQUE,error_code TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 first_attempt_at TIMESTAMPTZ,checked_at TIMESTAMPTZ,completed_at TIMESTAMPTZ,
 manual_confirmed BOOLEAN NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS campaign_refunds_one_pending ON campaign_refunds(campaign_id) WHERE status IN ('created','pending','review');
