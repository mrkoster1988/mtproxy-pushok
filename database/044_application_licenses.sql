-- Лицензии установок продаются центральным сервером; рекламодатели их не покупают.
CREATE TABLE IF NOT EXISTS app_license_settings(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),enabled BOOLEAN NOT NULL DEFAULT false);
INSERT INTO app_license_settings DEFAULT VALUES ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS app_license_plans(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),name TEXT NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 80),
 days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 3650),price_xtr INTEGER NOT NULL CHECK(price_xtr BETWEEN 1 AND 1000000),
 max_nodes INTEGER CHECK(max_nodes BETWEEN 0 AND 1000000),max_admins INTEGER CHECK(max_admins BETWEEN 1 AND 1000000),
 max_campaigns INTEGER CHECK(max_campaigns BETWEEN 0 AND 1000000),active BOOLEAN NOT NULL DEFAULT false,
 revision INTEGER NOT NULL DEFAULT 1,created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_license_installations(id TEXT PRIMARY KEY CHECK(length(id)=64),public_key TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS app_license_orders(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),installation_id TEXT NOT NULL REFERENCES app_license_installations(id),
 payer_id BIGINT NOT NULL CHECK(payer_id>0),plan_id UUID NOT NULL REFERENCES app_license_plans(id),terms JSONB NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>0),test_mode BOOLEAN NOT NULL,bot_id BIGINT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','closed','refunded')),
 invoice_url TEXT,precheckout_id TEXT UNIQUE,precheckout_at TIMESTAMPTZ,charge_id TEXT UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '15 minutes'
);
CREATE INDEX IF NOT EXISTS app_license_orders_installation ON app_license_orders(installation_id,created_at DESC);
CREATE TABLE IF NOT EXISTS app_license_grants(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),order_id UUID NOT NULL UNIQUE REFERENCES app_license_orders(id),
 installation_id TEXT NOT NULL REFERENCES app_license_installations(id),terms JSONB NOT NULL,
 starts_at TIMESTAMPTZ NOT NULL,ends_at TIMESTAMPTZ NOT NULL,revoked_at TIMESTAMPTZ,CHECK(ends_at>starts_at)
);
ALTER TABLE stars_payment_receipts ADD COLUMN IF NOT EXISTS app_license_order_id UUID REFERENCES app_license_orders(id);
CREATE TABLE IF NOT EXISTS app_license_refunds(charge_id TEXT PRIMARY KEY,payer_id BIGINT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),bot_id BIGINT NOT NULL,test_mode BOOLEAN NOT NULL,applied BOOLEAN NOT NULL DEFAULT false);
-- Локальный кэш установки: заполняется только после проверки подписи продавца.
CREATE TABLE IF NOT EXISTS app_license_state(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),required BOOLEAN NOT NULL DEFAULT false,
 installation_id TEXT,envelope JSONB,checked_at TIMESTAMPTZ,last_error TEXT);
INSERT INTO app_license_state DEFAULT VALUES ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION pushok_app_license_quota() RETURNS trigger AS $$
DECLARE state app_license_state%ROWTYPE; grant JSONB; cap INTEGER; used INTEGER; kind TEXT;
BEGIN
 SELECT * INTO state FROM app_license_state WHERE singleton;
 IF NOT state.required THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='users' THEN
  IF NEW.role<>'admin' THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' THEN IF OLD.role='admin' THEN RETURN NEW; END IF; END IF;
  kind:='max_admins';
 ELSE
  IF TG_OP='UPDATE' THEN IF OLD.archived_at IS NULL OR NEW.archived_at IS NOT NULL THEN RETURN NEW; END IF; END IF;
  kind:=CASE WHEN TG_TABLE_NAME='proxy_nodes' THEN 'max_nodes' ELSE 'max_campaigns' END;
 END IF;
 PERFORM pg_advisory_xact_lock(803136);
 -- Первому администратору нужен доступ для покупки лицензии при первоначальной установке.
 IF kind='max_admins' AND NOT EXISTS(SELECT 1 FROM users WHERE role='admin' AND id<>NEW.id) THEN RETURN NEW; END IF;
 IF state.envelope IS NULL OR (state.envelope->'payload'->>'lease_until')::BIGINT<=extract(epoch FROM now()) THEN RAISE EXCEPTION 'application_license_required'; END IF;
 SELECT g INTO grant FROM jsonb_array_elements(state.envelope->'payload'->'grants') g
  WHERE (g->>'starts_at')::BIGINT<=extract(epoch FROM now()) AND (g->>'ends_at')::BIGINT>extract(epoch FROM now())
  ORDER BY (g->>'starts_at')::BIGINT DESC LIMIT 1;
 IF grant IS NULL THEN RAISE EXCEPTION 'application_license_required'; END IF;
 cap:=(grant->'terms'->>kind)::INTEGER;
 IF cap IS NULL THEN RETURN NEW; END IF;
 IF kind='max_nodes' THEN SELECT count(*) INTO used FROM proxy_nodes WHERE archived_at IS NULL AND id<>NEW.id;
 ELSIF kind='max_admins' THEN SELECT count(*) INTO used FROM users WHERE role='admin' AND id<>NEW.id;
 ELSE SELECT count(*) INTO used FROM campaigns WHERE archived_at IS NULL AND placement_status NOT IN ('completed','cancelled') AND moderation_status<>'rejected' AND id<>NEW.id;
 END IF;
 IF used>=cap THEN RAISE EXCEPTION 'application_license_limit_%',kind; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS app_license_nodes ON proxy_nodes;
CREATE TRIGGER app_license_nodes BEFORE INSERT OR UPDATE OF archived_at ON proxy_nodes FOR EACH ROW EXECUTE FUNCTION pushok_app_license_quota();
DROP TRIGGER IF EXISTS app_license_admins ON users;
CREATE TRIGGER app_license_admins BEFORE INSERT OR UPDATE OF role ON users FOR EACH ROW EXECUTE FUNCTION pushok_app_license_quota();
DROP TRIGGER IF EXISTS app_license_campaigns ON campaigns;
CREATE TRIGGER app_license_campaigns BEFORE INSERT OR UPDATE OF archived_at ON campaigns FOR EACH ROW EXECUTE FUNCTION pushok_app_license_quota();
