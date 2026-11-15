-- Баланс хранится в минимальных единицах: копейки RUB/BONUS, целые XTR.
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS login_surface TEXT NOT NULL DEFAULT 'web';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS purchase_surface TEXT NOT NULL DEFAULT 'web';
CREATE TABLE IF NOT EXISTS wallet_accounts (
 user_id UUID NOT NULL REFERENCES users(id),
 currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR','BONUS')),
 balance BIGINT NOT NULL DEFAULT 0 CHECK(abs(balance)<=1000000000000),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,currency)
);
CREATE TABLE IF NOT EXISTS wallet_preferences (
 user_id UUID PRIMARY KEY REFERENCES users(id),
 auto_pay BOOLEAN NOT NULL DEFAULT false,
 currency TEXT NOT NULL DEFAULT 'XTR' CHECK(currency IN ('RUB','XTR')),
 per_campaign_limit BIGINT NOT NULL DEFAULT 0 CHECK(per_campaign_limit BETWEEN 0 AND 100000000),
 use_bonus BOOLEAN NOT NULL DEFAULT true,
 CHECK(NOT auto_pay OR per_campaign_limit>0)
);
CREATE TABLE IF NOT EXISTS wallet_topups (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),user_id UUID NOT NULL REFERENCES users(id),
 request_id UUID NOT NULL, provider TEXT NOT NULL CHECK(provider IN ('sbp','telegram_stars')),
 currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR')),amount BIGINT NOT NULL CHECK(amount>0 AND amount<=100000000),
 status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','pending','succeeded','failed','refunded','review')),
 provider_payment_id TEXT,provider_account TEXT NOT NULL,test_mode BOOLEAN NOT NULL,
 invoice_url TEXT,return_url TEXT,expires_at TIMESTAMPTZ,
 precheckout_query_id TEXT UNIQUE,precheckout_approved_at TIMESTAMPTZ,
 refunded_amount BIGINT NOT NULL DEFAULT 0 CHECK(refunded_amount>=0 AND refunded_amount<=amount),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),settled_at TIMESTAMPTZ,
 next_check_at TIMESTAMPTZ NOT NULL DEFAULT now(),check_failures INTEGER NOT NULL DEFAULT 0,
 review_reason TEXT,
 UNIQUE(user_id,request_id),UNIQUE(provider,provider_payment_id),
 CHECK((provider='sbp' AND currency='RUB') OR (provider='telegram_stars' AND currency='XTR'))
);
ALTER TABLE stars_payment_receipts ADD COLUMN IF NOT EXISTS wallet_topup_id UUID REFERENCES wallet_topups(id);
CREATE TABLE IF NOT EXISTS wallet_payments (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),campaign_id UUID NOT NULL UNIQUE REFERENCES campaigns(id),
 user_id UUID NOT NULL REFERENCES users(id),currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR')),
 cash_amount BIGINT NOT NULL CHECK(cash_amount>=0),bonus_amount BIGINT NOT NULL DEFAULT 0 CHECK(bonus_amount>=0),
 status TEXT NOT NULL DEFAULT 'paid' CHECK(status IN ('paid','refunded')),
 automatic BOOLEAN NOT NULL DEFAULT false,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),refunded_at TIMESTAMPTZ,
 CHECK(cash_amount+bonus_amount>0),CHECK(currency='RUB' OR bonus_amount=0)
);
CREATE TABLE IF NOT EXISTS wallet_entries (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),user_id UUID NOT NULL REFERENCES users(id),
 currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR','BONUS')),
 delta BIGINT NOT NULL CHECK(delta<>0 AND abs(delta)<=1000000000000),balance_after BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('topup','payment','refund','manual_credit','manual_debit','reversal','provider_refund')),
 operation_key TEXT NOT NULL UNIQUE,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 3 AND 500),
 actor_user_id UUID REFERENCES users(id),campaign_id UUID REFERENCES campaigns(id),topup_id UUID REFERENCES wallet_topups(id),
 reversal_of UUID UNIQUE REFERENCES wallet_entries(id),created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,currency) REFERENCES wallet_accounts(user_id,currency)
);
CREATE INDEX IF NOT EXISTS wallet_entries_user_date ON wallet_entries(user_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS wallet_topups_due ON wallet_topups(next_check_at) WHERE provider='sbp' AND status IN ('created','pending','succeeded');
CREATE OR REPLACE FUNCTION pushok_wallet_journal() RETURNS trigger AS $$
DECLARE old_balance BIGINT;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'wallet_journal_immutable'; END IF;
 SELECT balance INTO old_balance FROM wallet_accounts WHERE user_id=NEW.user_id AND currency=NEW.currency FOR UPDATE;
 IF NOT FOUND OR NEW.balance_after<>old_balance+NEW.delta THEN RAISE EXCEPTION 'wallet_balance_mismatch'; END IF;
 IF NEW.delta<0 AND NEW.balance_after<0 AND NEW.kind<>'provider_refund' THEN RAISE EXCEPTION 'wallet_insufficient_funds'; END IF;
 UPDATE wallet_accounts SET balance=NEW.balance_after,updated_at=now() WHERE user_id=NEW.user_id AND currency=NEW.currency;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS wallet_journal_post ON wallet_entries;
CREATE TRIGGER wallet_journal_post AFTER INSERT ON wallet_entries FOR EACH ROW EXECUTE FUNCTION pushok_wallet_journal();
DROP TRIGGER IF EXISTS wallet_journal_immutable ON wallet_entries;
CREATE TRIGGER wallet_journal_immutable BEFORE UPDATE OR DELETE ON wallet_entries FOR EACH ROW EXECUTE FUNCTION pushok_wallet_journal();

-- Оплата денежной частью баланса участвует в реферальной программе.
-- Полностью бонусная оплата, бесплатные и тестовые платежи награды не дают.
CREATE OR REPLACE FUNCTION pushok_referral_reward() RETURNS trigger AS $$
DECLARE r growth_referrals%ROWTYPE;
BEGIN
 IF NEW.placement_status='completed' AND NEW.payment_status='verified'
    AND (OLD.placement_status IS DISTINCT FROM NEW.placement_status OR OLD.payment_status IS DISTINCT FROM NEW.payment_status)
    AND NEW.price_kopecks_snapshot>0
    AND (EXISTS(SELECT 1 FROM payment_attempts WHERE campaign_id=NEW.id AND status='succeeded' AND NOT telegram_test_environment)
         OR EXISTS(SELECT 1 FROM wallet_payments WHERE campaign_id=NEW.id AND status='paid' AND cash_amount>0)) THEN
   SELECT * INTO r FROM growth_referrals WHERE referred_user_id=NEW.advertiser_user_id AND created_at<=NEW.created_at;
   IF FOUND THEN
     INSERT INTO growth_offers(kind,name,code,percent,total_limit,recipient_user_id,reward_for_user_id,reward_campaign_id,ends_at)
     VALUES('referral','За приглашённого рекламодателя',upper(replace(gen_random_uuid()::text,'-','')),r.reward_percent,1,r.inviter_user_id,r.referred_user_id,NEW.id,now()+make_interval(days=>r.reward_days))
     ON CONFLICT(reward_for_user_id) DO NOTHING;
   END IF;
 END IF;
 IF NEW.payment_status='refunded' AND OLD.payment_status IS DISTINCT FROM NEW.payment_status THEN
   UPDATE growth_offers SET active=false WHERE reward_campaign_id=NEW.id;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- Возврат может прийти раньше подтверждения оплаты: сохраняем его для сверки.
CREATE TABLE IF NOT EXISTS wallet_star_refunds (
 charge_id TEXT PRIMARY KEY,telegram_user_id BIGINT NOT NULL,currency TEXT NOT NULL,
 amount BIGINT NOT NULL,provider_account TEXT NOT NULL,test_mode BOOLEAN NOT NULL,
 status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received','applied','review')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS wallet_checked_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch';
CREATE INDEX IF NOT EXISTS campaigns_wallet_queue ON campaigns(wallet_checked_at,id)
 WHERE payment_status='awaiting_payment' AND moderation_status='approved' AND placement_status='not_scheduled';
