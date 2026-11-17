ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS refunded_amount BIGINT NOT NULL DEFAULT 0 CHECK(refunded_amount>=0);
ALTER TABLE wallet_payments ADD COLUMN IF NOT EXISTS refunded_cash BIGINT NOT NULL DEFAULT 0 CHECK(refunded_cash>=0 AND refunded_cash<=cash_amount);
ALTER TABLE wallet_payments ADD COLUMN IF NOT EXISTS refunded_bonus BIGINT NOT NULL DEFAULT 0 CHECK(refunded_bonus>=0 AND refunded_bonus<=bonus_amount);
UPDATE wallet_payments SET refunded_cash=cash_amount,refunded_bonus=bonus_amount WHERE status='refunded';
UPDATE campaigns c SET refunded_amount=p.cash_amount+p.bonus_amount FROM wallet_payments p WHERE p.campaign_id=c.id AND p.status='refunded' AND c.refunded_amount=0;
UPDATE campaigns c SET refunded_amount=a.amount FROM (SELECT campaign_id,sum(amount) amount FROM payment_attempts WHERE status='refunded' GROUP BY campaign_id) a WHERE a.campaign_id=c.id AND c.refunded_amount=0;
UPDATE campaigns c SET refunded_amount=price_kopecks_snapshot WHERE payment_status='refunded' AND refunded_amount=0 AND NOT EXISTS(SELECT 1 FROM payment_attempts a WHERE a.campaign_id=c.id) AND NOT EXISTS(SELECT 1 FROM wallet_payments w WHERE w.campaign_id=c.id);
CREATE TABLE IF NOT EXISTS campaign_refunds (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),request_id UUID NOT NULL UNIQUE,
 campaign_id UUID NOT NULL REFERENCES campaigns(id),actor_id UUID NOT NULL REFERENCES users(id),
 source TEXT NOT NULL CHECK(source IN ('wallet','sbp','telegram_stars','manual')),
 currency TEXT NOT NULL CHECK(currency IN ('RUB','XTR')),
 amount BIGINT NOT NULL CHECK(amount>0),reason TEXT NOT NULL CHECK(length(reason) BETWEEN 3 AND 500),
 status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','pending','succeeded','canceled','review')),
 payment_id UUID, paid_amount BIGINT NOT NULL CHECK(paid_amount>0 AND amount<=paid_amount),
 requested_amount BIGINT, provider_refund_id TEXT UNIQUE,error_code TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 first_attempt_at TIMESTAMPTZ,checked_at TIMESTAMPTZ,completed_at TIMESTAMPTZ,
 manual_confirmed BOOLEAN NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS campaign_refunds_one_pending ON campaign_refunds(campaign_id) WHERE status IN ('created','pending','review');

-- Частичный возврат также отменяет награду; возвращённый заказ её повторно не выдаёт.
CREATE OR REPLACE FUNCTION pushok_referral_reward() RETURNS trigger AS $$
DECLARE r growth_referrals%ROWTYPE;
        reward growth_wallet_rewards%ROWTYPE;
        balance_now BIGINT;
        posted UUID;
BEGIN
 IF NEW.placement_status='completed' AND NEW.payment_status='verified'
    AND (OLD.placement_status IS DISTINCT FROM NEW.placement_status OR OLD.payment_status IS DISTINCT FROM NEW.payment_status)
    AND NEW.price_kopecks_snapshot>0 AND NEW.refunded_amount=0
    AND (EXISTS(SELECT 1 FROM payment_attempts WHERE campaign_id=NEW.id AND status='succeeded'
                AND provider='sbp' AND currency='RUB' AND sbp_test_mode IS FALSE)
         OR EXISTS(SELECT 1 FROM wallet_payments WHERE campaign_id=NEW.id AND status='paid' AND currency='RUB' AND cash_amount>0)) THEN
   -- Два одновременно завершившихся размещения одного друга дают одну награду.
   SELECT * INTO r FROM growth_referrals WHERE referred_user_id=NEW.advertiser_user_id AND created_at<=NEW.created_at FOR UPDATE;
   IF FOUND AND NOT EXISTS(SELECT 1 FROM growth_offers WHERE reward_for_user_id=r.referred_user_id)
            AND NOT EXISTS(SELECT 1 FROM growth_wallet_rewards WHERE referred_user_id=r.referred_user_id) THEN
     IF r.reward_mode='coupon' THEN
       INSERT INTO growth_offers(kind,name,code,percent,total_limit,recipient_user_id,reward_for_user_id,reward_campaign_id,ends_at)
       VALUES('referral','За приглашённого рекламодателя',upper(replace(gen_random_uuid()::text,'-','')),r.reward_percent,1,r.inviter_user_id,r.referred_user_id,NEW.id,now()+make_interval(days=>r.reward_days))
       ON CONFLICT(reward_for_user_id) DO NOTHING;
     ELSE
       INSERT INTO wallet_accounts(user_id,currency) VALUES(r.inviter_user_id,'BONUS') ON CONFLICT DO NOTHING;
       SELECT balance INTO balance_now FROM wallet_accounts WHERE user_id=r.inviter_user_id AND currency='BONUS' FOR UPDATE;
       INSERT INTO wallet_entries(user_id,currency,delta,balance_after,kind,operation_key,reason,campaign_id)
       VALUES(r.inviter_user_id,'BONUS',r.reward_bonus_kopecks,balance_now+r.reward_bonus_kopecks,'referral_reward',
         'referral:'||r.referred_user_id,'Награда за первое оплаченное и завершённое размещение приглашённого',NEW.id) RETURNING id INTO posted;
       INSERT INTO growth_wallet_rewards(referred_user_id,inviter_user_id,campaign_id,amount,entry_id)
       VALUES(r.referred_user_id,r.inviter_user_id,NEW.id,r.reward_bonus_kopecks,posted);
     END IF;
   END IF;
 END IF;
 IF (NEW.payment_status='refunded' AND OLD.payment_status IS DISTINCT FROM NEW.payment_status) OR NEW.refunded_amount>OLD.refunded_amount THEN
   UPDATE growth_offers SET active=false WHERE reward_campaign_id=NEW.id;
   SELECT * INTO reward FROM growth_wallet_rewards WHERE campaign_id=NEW.id FOR UPDATE;
   IF FOUND AND reward.reversal_id IS NULL THEN
     SELECT balance INTO balance_now FROM wallet_accounts WHERE user_id=reward.inviter_user_id AND currency='BONUS' FOR UPDATE;
     INSERT INTO wallet_entries(user_id,currency,delta,balance_after,kind,operation_key,reason,campaign_id,reversal_of)
     VALUES(reward.inviter_user_id,'BONUS',-reward.amount,balance_now-reward.amount,'referral_reversal',
       'referral-refund:'||reward.referred_user_id,'Отмена реферальной награды: оплата размещения приглашённого возвращена',NEW.id,reward.entry_id) RETURNING id INTO posted;
     UPDATE growth_wallet_rewards SET reversal_id=posted,reversed_at=clock_timestamp() WHERE referred_user_id=reward.referred_user_id;
   END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- Возврат и планирование используют одну блокировку строки кампании.
-- Защита действует и на ручные/автоматические маршруты назначения слота.
CREATE OR REPLACE FUNCTION pushok_refund_placement_guard() RETURNS trigger AS $$
BEGIN
 IF NEW.placement_status IN ('scheduled','active') AND
    (NEW.refunded_amount>0 OR EXISTS(SELECT 1 FROM campaign_refunds WHERE campaign_id=NEW.id AND status IN ('created','pending','review'))) THEN
  RAISE EXCEPTION 'campaign_refund_blocks_placement' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS campaign_refund_placement_guard ON campaigns;
CREATE TRIGGER campaign_refund_placement_guard BEFORE UPDATE ON campaigns
 FOR EACH ROW EXECUTE FUNCTION pushok_refund_placement_guard();
