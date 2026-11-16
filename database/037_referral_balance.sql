-- Старые приглашения сохраняют купон. Новые могут давать фиксированные бонусные рубли.
ALTER TABLE growth_settings ADD COLUMN IF NOT EXISTS reward_mode TEXT NOT NULL DEFAULT 'coupon' CHECK(reward_mode IN ('coupon','bonus'));
ALTER TABLE growth_settings ADD COLUMN IF NOT EXISTS reward_bonus_kopecks INTEGER NOT NULL DEFAULT 10000 CHECK(reward_bonus_kopecks BETWEEN 1 AND 1000000);
ALTER TABLE growth_referrals ADD COLUMN IF NOT EXISTS reward_mode TEXT NOT NULL DEFAULT 'coupon' CHECK(reward_mode IN ('coupon','bonus'));
ALTER TABLE growth_referrals ADD COLUMN IF NOT EXISTS reward_bonus_kopecks INTEGER NOT NULL DEFAULT 10000 CHECK(reward_bonus_kopecks BETWEEN 1 AND 1000000);

ALTER TABLE wallet_entries DROP CONSTRAINT IF EXISTS wallet_entries_kind_check;
ALTER TABLE wallet_entries ADD CONSTRAINT wallet_entries_kind_check CHECK(kind IN
 ('topup','payment','refund','manual_credit','manual_debit','reversal','provider_refund','referral_reward','referral_reversal','gift_credit'));
CREATE TABLE IF NOT EXISTS growth_wallet_rewards (
 referred_user_id UUID PRIMARY KEY REFERENCES growth_referrals(referred_user_id),
 inviter_user_id UUID NOT NULL REFERENCES users(id),
 campaign_id UUID NOT NULL UNIQUE REFERENCES campaigns(id),
 amount BIGINT NOT NULL CHECK(amount BETWEEN 1 AND 1000000),
 entry_id UUID NOT NULL UNIQUE REFERENCES wallet_entries(id),
 reversal_id UUID UNIQUE REFERENCES wallet_entries(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 reversed_at TIMESTAMPTZ,
 CHECK((reversal_id IS NULL)=(reversed_at IS NULL))
);
CREATE INDEX IF NOT EXISTS growth_wallet_rewards_inviter ON growth_wallet_rewards(inviter_user_id,created_at DESC);

-- Потраченная награда при возврате не исчезает: отдельная проводка создаёт
-- бонусную задолженность, которая погашается последующими начислениями.
CREATE OR REPLACE FUNCTION pushok_wallet_journal() RETURNS trigger AS $$
DECLARE old_balance BIGINT;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'wallet_journal_immutable'; END IF;
 SELECT balance INTO old_balance FROM wallet_accounts WHERE user_id=NEW.user_id AND currency=NEW.currency FOR UPDATE;
 IF NOT FOUND OR NEW.balance_after<>old_balance+NEW.delta THEN RAISE EXCEPTION 'wallet_balance_mismatch'; END IF;
 IF NEW.delta<0 AND NEW.balance_after<0 AND NEW.kind NOT IN ('provider_refund','referral_reversal') THEN
   RAISE EXCEPTION 'wallet_insufficient_funds';
 END IF;
 UPDATE wallet_accounts SET balance=NEW.balance_after,updated_at=now() WHERE user_id=NEW.user_id AND currency=NEW.currency;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pushok_referral_reward() RETURNS trigger AS $$
DECLARE r growth_referrals%ROWTYPE;
        reward growth_wallet_rewards%ROWTYPE;
        balance_now BIGINT;
        posted UUID;
BEGIN
 IF NEW.placement_status='completed' AND NEW.payment_status='verified'
    AND (OLD.placement_status IS DISTINCT FROM NEW.placement_status OR OLD.payment_status IS DISTINCT FROM NEW.payment_status)
    AND NEW.price_kopecks_snapshot>0
    AND (EXISTS(SELECT 1 FROM payment_attempts WHERE campaign_id=NEW.id AND status='succeeded'
                AND ((provider='telegram_stars' AND NOT telegram_test_environment)
                     OR (provider='sbp' AND sbp_test_mode IS FALSE)))
         OR EXISTS(SELECT 1 FROM wallet_payments WHERE campaign_id=NEW.id AND status='paid' AND cash_amount>0)) THEN
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
 IF NEW.payment_status='refunded' AND OLD.payment_status IS DISTINCT FROM NEW.payment_status THEN
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
