-- Служебный бесплатный тариф доступен только пользователям с ролью admin.
ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS admin_only BOOLEAN NOT NULL DEFAULT FALSE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'tariffs'::regclass AND conname = 'tariffs_admin_free_check') THEN
    ALTER TABLE tariffs ADD CONSTRAINT tariffs_admin_free_check
      CHECK (NOT admin_only OR (price_kopecks = 0 AND stars_price_xtr IS NULL));
  END IF;
END;
$$;

INSERT INTO tariffs(id, name, duration_seconds, price_kopecks, currency, admin_only)
VALUES ('7f5b60e4-ecf1-4bc3-9e3a-cd5153f4a001', 'Администратор', 86400, 0, 'RUB', TRUE)
ON CONFLICT (id) DO NOTHING;

-- Защита применяется и к вставкам из фоновых сервисов, не только к веб-форме.
CREATE OR REPLACE FUNCTION pushok_check_admin_tariff() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM tariffs WHERE id = NEW.tariff_id AND admin_only)
     AND NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.advertiser_user_id AND role = 'admin') THEN
    RAISE EXCEPTION 'admin_tariff_required' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pushok_admin_tariff_trigger ON campaigns;
CREATE TRIGGER pushok_admin_tariff_trigger
BEFORE INSERT OR UPDATE OF tariff_id, advertiser_user_id ON campaigns
FOR EACH ROW EXECUTE FUNCTION pushok_check_admin_tariff();
