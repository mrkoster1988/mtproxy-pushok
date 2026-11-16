-- Лицензия кабинета: включается владельцем, не останавливает оплаченные кампании.
CREATE TABLE IF NOT EXISTS license_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton), enabled BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO license_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS license_plans (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),name TEXT NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 80),
 days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 3650),price_xtr INTEGER NOT NULL CHECK(price_xtr BETWEEN 0 AND 1000000),
 max_channels INTEGER CHECK(max_channels BETWEEN 0 AND 1000000),
 max_open_campaigns INTEGER CHECK(max_open_campaigns BETWEEN 0 AND 1000000),
 max_campaigns_per_period INTEGER CHECK(max_campaigns_per_period BETWEEN 0 AND 1000000),
 active BOOLEAN NOT NULL DEFAULT false,revision INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS license_orders (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),user_id UUID NOT NULL REFERENCES users(id),plan_id UUID NOT NULL REFERENCES license_plans(id),
 terms JSONB NOT NULL,amount INTEGER NOT NULL CHECK(amount BETWEEN 0 AND 1000000),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','closed','refunded')),
 test_mode BOOLEAN NOT NULL DEFAULT false,bot_id BIGINT,invoice_url TEXT,
 precheckout_id TEXT UNIQUE,precheckout_at TIMESTAMPTZ,charge_id TEXT UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '15 minutes',paid_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS license_orders_user ON license_orders(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS license_grants (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),user_id UUID NOT NULL REFERENCES users(id),order_id UUID UNIQUE REFERENCES license_orders(id),
 terms JSONB NOT NULL,starts_at TIMESTAMPTZ NOT NULL,ends_at TIMESTAMPTZ NOT NULL,revoked_at TIMESTAMPTZ,
 CHECK(ends_at>starts_at)
);
CREATE INDEX IF NOT EXISTS license_grants_user ON license_grants(user_id,ends_at) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS license_campaign_usage (
 campaign_id UUID PRIMARY KEY REFERENCES campaigns(id),grant_id UUID NOT NULL REFERENCES license_grants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE stars_payment_receipts ADD COLUMN IF NOT EXISTS license_order_id UUID REFERENCES license_orders(id);
CREATE TABLE IF NOT EXISTS license_refunds (
 charge_id TEXT PRIMARY KEY,telegram_user_id BIGINT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),
 bot_id BIGINT NOT NULL,test_mode BOOLEAN NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),applied BOOLEAN NOT NULL DEFAULT false
);

CREATE OR REPLACE FUNCTION pushok_license_quota() RETURNS trigger AS $$
DECLARE uid UUID; g license_grants%ROWTYPE; cap INTEGER; used INTEGER; member_role TEXT;
BEGIN
 IF NOT (SELECT enabled FROM license_settings WHERE singleton) THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND (OLD.archived_at IS NULL OR NEW.archived_at IS NOT NULL) THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='sponsor_channels' THEN uid:=NEW.owner_user_id; ELSE uid:=NEW.advertiser_user_id; END IF;
 -- Один счётчик на пользователя, включая параллельные API/бот/восстановление.
 SELECT role INTO member_role FROM users WHERE id=uid FOR UPDATE;
 IF member_role IN ('admin','operator') THEN RETURN NEW; END IF;
 -- INSERT ON CONFLICT не должен повторно расходовать лимит.
 IF TG_OP='INSERT' AND TG_TABLE_NAME='sponsor_channels' THEN
  IF EXISTS(SELECT 1 FROM sponsor_channels WHERE owner_user_id=uid AND telegram_url=NEW.telegram_url) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='campaigns' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.bot_request_key IS NOT NULL AND EXISTS(SELECT 1 FROM campaigns WHERE bot_request_key=NEW.bot_request_key AND advertiser_user_id=uid) THEN RETURN NEW; END IF;
  END IF;
 END IF;
 SELECT * INTO g FROM license_grants WHERE user_id=uid AND revoked_at IS NULL
  AND starts_at<=now() AND ends_at>now() ORDER BY starts_at DESC,id LIMIT 1;
 IF NOT FOUND THEN RAISE EXCEPTION 'license_required'; END IF;
 IF TG_TABLE_NAME='sponsor_channels' THEN
  cap:=(g.terms->>'max_channels')::INTEGER;
  SELECT count(*) INTO used FROM sponsor_channels WHERE owner_user_id=uid AND archived_at IS NULL AND id<>NEW.id;
  IF cap IS NOT NULL AND used>=cap THEN RAISE EXCEPTION 'license_channels_limit'; END IF;
 ELSE
  cap:=(g.terms->>'max_open_campaigns')::INTEGER;
  SELECT count(*) INTO used FROM campaigns WHERE advertiser_user_id=uid AND archived_at IS NULL
   AND placement_status NOT IN ('completed','cancelled') AND moderation_status<>'rejected' AND id<>NEW.id;
  IF cap IS NOT NULL AND used>=cap THEN RAISE EXCEPTION 'license_open_campaigns_limit'; END IF;
  IF TG_OP='INSERT' THEN
   cap:=(g.terms->>'max_campaigns_per_period')::INTEGER;
   SELECT count(*) INTO used FROM license_campaign_usage WHERE grant_id=g.id;
   IF cap IS NOT NULL AND used>=cap THEN RAISE EXCEPTION 'license_period_limit'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION pushok_license_usage() RETURNS trigger AS $$
BEGIN
 IF (SELECT enabled FROM license_settings WHERE singleton) THEN
  INSERT INTO license_campaign_usage(campaign_id,grant_id)
   SELECT NEW.id,g.id FROM license_grants g WHERE g.user_id=NEW.advertiser_user_id AND g.revoked_at IS NULL
    AND g.starts_at<=now() AND g.ends_at>now() ORDER BY g.starts_at DESC,g.id LIMIT 1 ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS license_channel_quota ON sponsor_channels;
CREATE TRIGGER license_channel_quota BEFORE INSERT OR UPDATE OF archived_at ON sponsor_channels FOR EACH ROW EXECUTE FUNCTION pushok_license_quota();
DROP TRIGGER IF EXISTS license_campaign_quota ON campaigns;
CREATE TRIGGER license_campaign_quota BEFORE INSERT OR UPDATE OF archived_at ON campaigns FOR EACH ROW EXECUTE FUNCTION pushok_license_quota();
DROP TRIGGER IF EXISTS license_campaign_usage ON campaigns;
CREATE TRIGGER license_campaign_usage AFTER INSERT ON campaigns FOR EACH ROW EXECUTE FUNCTION pushok_license_usage();
