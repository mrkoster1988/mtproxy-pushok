-- Промокоды, подарочные размещения, скидки и реферальные купоны.
CREATE TABLE IF NOT EXISTS growth_settings (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
    referrals_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reward_percent INTEGER NOT NULL DEFAULT 10 CHECK(reward_percent BETWEEN 1 AND 100),
    reward_days INTEGER NOT NULL DEFAULT 90 CHECK(reward_days BETWEEN 1 AND 365)
);
INSERT INTO growth_settings DEFAULT VALUES ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS growth_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    code TEXT NOT NULL UNIQUE DEFAULT upper(replace(gen_random_uuid()::text,'-','')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS growth_referrals (
    referred_user_id UUID PRIMARY KEY REFERENCES users(id),
    inviter_user_id UUID NOT NULL REFERENCES users(id),
    reward_percent INTEGER NOT NULL CHECK(reward_percent BETWEEN 1 AND 100),
    reward_days INTEGER NOT NULL CHECK(reward_days BETWEEN 1 AND 365),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(referred_user_id <> inviter_user_id)
);
CREATE TABLE IF NOT EXISTS growth_offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK(kind IN ('discount','promo','gift','referral')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    code TEXT UNIQUE,
    percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
    tariff_id UUID REFERENCES tariffs(id),
    min_duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK(min_duration_seconds >= 0),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at TIMESTAMPTZ,
    total_limit INTEGER CHECK(total_limit > 0),
    per_user_limit INTEGER NOT NULL DEFAULT 1 CHECK(per_user_limit > 0),
    recipient_user_id UUID REFERENCES users(id),
    reward_for_user_id UUID UNIQUE REFERENCES growth_referrals(referred_user_id),
    reward_campaign_id UUID REFERENCES campaigns(id),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(ends_at IS NULL OR ends_at > starts_at),
    CHECK((kind='discount' AND code IS NULL) OR (kind<>'discount' AND code IS NOT NULL)),
    CHECK(kind<>'gift' OR (percent=100 AND total_limit=1 AND tariff_id IS NOT NULL)),
    CHECK(kind<>'referral' OR (recipient_user_id IS NOT NULL AND reward_for_user_id IS NOT NULL AND total_limit=1))
);
CREATE TABLE IF NOT EXISTS growth_redemptions (
    campaign_id UUID PRIMARY KEY REFERENCES campaigns(id),
    offer_id UUID NOT NULL REFERENCES growth_offers(id),
    user_id UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS growth_redemptions_offer_user ON growth_redemptions(offer_id,user_id);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS pricing_version INTEGER;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS base_price_kopecks_snapshot INTEGER CHECK(base_price_kopecks_snapshot >= 0);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS stars_price_snapshot INTEGER CHECK(stars_price_snapshot >= 0);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS growth_offer_id UUID REFERENCES growth_offers(id);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS growth_offer_name TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS discount_percent_snapshot INTEGER CHECK(discount_percent_snapshot BETWEEN 1 AND 100);

-- Начисление после завершения оплаченного размещения. Бесплатные и ручные оплаты
-- не дают бонус. UNIQUE исключает повторное начисление при повторах агента.
CREATE OR REPLACE FUNCTION pushok_referral_reward() RETURNS trigger AS $$
DECLARE r growth_referrals%ROWTYPE;
BEGIN
  IF NEW.placement_status='completed' AND NEW.payment_status='verified'
     AND (OLD.placement_status IS DISTINCT FROM NEW.placement_status OR OLD.payment_status IS DISTINCT FROM NEW.payment_status)
     AND NEW.price_kopecks_snapshot > 0
     AND EXISTS(SELECT 1 FROM payment_attempts WHERE campaign_id=NEW.id AND status='succeeded' AND NOT telegram_test_environment) THEN
    SELECT * INTO r FROM growth_referrals WHERE referred_user_id=NEW.advertiser_user_id AND created_at <= NEW.created_at;
    IF FOUND THEN
      INSERT INTO growth_offers(kind,name,code,percent,total_limit,recipient_user_id,reward_for_user_id,reward_campaign_id,ends_at)
      VALUES('referral','За приглашённого рекламодателя',upper(replace(gen_random_uuid()::text,'-','')),
             r.reward_percent,1,r.inviter_user_id,r.referred_user_id,NEW.id,now()+make_interval(days=>r.reward_days))
      ON CONFLICT(reward_for_user_id) DO NOTHING;
    END IF;
  END IF;
  IF NEW.payment_status='refunded' AND OLD.payment_status IS DISTINCT FROM NEW.payment_status THEN
    UPDATE growth_offers SET active=FALSE WHERE reward_campaign_id=NEW.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS campaign_referral_reward ON campaigns;
CREATE TRIGGER campaign_referral_reward AFTER UPDATE ON campaigns FOR EACH ROW EXECUTE FUNCTION pushok_referral_reward();
