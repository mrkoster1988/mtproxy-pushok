-- Snapshot referral terms until the installation owner can receive the registration.
ALTER TABLE growth_referrals ADD COLUMN IF NOT EXISTS rewards_enabled boolean NOT NULL DEFAULT true;
CREATE TABLE IF NOT EXISTS growth_direct_registrations (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    registered_at timestamptz NOT NULL DEFAULT now(),
    rewards_enabled boolean NOT NULL,
    reward_mode text NOT NULL,
    reward_percent integer NOT NULL,
    reward_days integer NOT NULL,
    reward_bonus_kopecks integer NOT NULL
);

CREATE OR REPLACE FUNCTION pushok_bind_direct_registrations(target uuid) RETURNS void AS $$
DECLARE developer uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(736102484);
    SELECT id INTO developer FROM users WHERE is_owner AND blocked_at IS NULL AND telegram_user_id > 0;
    IF developer IS NULL THEN RETURN; END IF;

    -- When the installation owner is appointed later, they may already have an inviter.
    WITH RECURSIVE ancestors(id) AS (
        SELECT developer UNION SELECT r.inviter_user_id FROM growth_referrals r JOIN ancestors a ON a.id=r.referred_user_id
    ), pending AS (
        DELETE FROM growth_direct_registrations WHERE target IS NULL OR user_id=target RETURNING *
    ), registered AS (
        INSERT INTO growth_referrals(referred_user_id,inviter_user_id,created_at,rewards_enabled,
            reward_mode,reward_percent,reward_days,reward_bonus_kopecks)
        SELECT p.user_id,developer,p.registered_at,p.rewards_enabled,p.reward_mode,p.reward_percent,p.reward_days,p.reward_bonus_kopecks
        FROM pending p WHERE NOT EXISTS (SELECT 1 FROM ancestors WHERE id=p.user_id)
        ON CONFLICT (referred_user_id) DO NOTHING RETURNING referred_user_id
    )
    INSERT INTO audit_events(actor_user_id,event_type,details_json)
        SELECT referred_user_id,'referral_bound',jsonb_build_object('source','direct_registration','inviter',developer)
        FROM registered;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pushok_bind_on_owner_assignment() RETURNS trigger AS $$
BEGIN
    PERFORM pushok_bind_direct_registrations(NULL);
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS bind_direct_on_owner_assignment ON users;
CREATE TRIGGER bind_direct_on_owner_assignment AFTER INSERT OR UPDATE OF is_owner,blocked_at ON users
    FOR EACH ROW WHEN (NEW.is_owner AND NEW.blocked_at IS NULL) EXECUTE FUNCTION pushok_bind_on_owner_assignment();

-- Preserve the reward and refund journal for existing referrals. Direct signups during
-- a pause count as people only and do not earn a payment when the program resumes.
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
   IF FOUND AND r.rewards_enabled AND NOT EXISTS(SELECT 1 FROM growth_offers WHERE reward_for_user_id=r.referred_user_id)
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
