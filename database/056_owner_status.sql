ALTER TABLE users ADD COLUMN IF NOT EXISTS is_owner BOOLEAN NOT NULL DEFAULT false;
UPDATE users SET is_owner=true WHERE id=(SELECT id FROM users WHERE role='admin' ORDER BY created_at,id LIMIT 1) AND NOT EXISTS(SELECT 1 FROM users WHERE is_owner);
CREATE UNIQUE INDEX IF NOT EXISTS users_single_owner ON users(is_owner) WHERE is_owner;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='owner_requires_admin') THEN ALTER TABLE users ADD CONSTRAINT owner_requires_admin CHECK(NOT is_owner OR role='admin'); END IF; END $$;
-- Объединяем настройки будущих призов; снимки открытых периодов не меняем.
DO $$ DECLARE cfg JSONB; k TEXT; item JSONB; prizes JSONB; BEGIN
 SELECT rewards INTO cfg FROM hall_settings WHERE singleton;
 FOREACH k IN ARRAY ARRAY['week','month','year'] LOOP
  item=coalesce(cfg->k,'{}'::jsonb);
  IF NOT (item ? 'spend') AND (item ? 'spend_rub' OR item ? 'spend_xtr') THEN
   SELECT jsonb_agg(coalesce((item->'spend_rub'->>n)::bigint,0)+coalesce((item->'spend_xtr'->>n)::bigint,0) ORDER BY n) INTO prizes FROM generate_series(0,9) n;
   item=(item-'spend_rub'-'spend_xtr')||jsonb_build_object('spend',prizes);
   cfg=jsonb_set(cfg,ARRAY[k],item,true);
  END IF;
 END LOOP;
 UPDATE hall_settings SET rewards=cfg WHERE singleton;
END $$;
