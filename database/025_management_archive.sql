-- Удаление из рабочих списков с сохранением оплаченной истории и аудита.
ALTER TABLE sponsor_channels ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE proxy_nodes ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS archived_state JSONB;
ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION pushok_management_reference_guard() RETURNS trigger AS $$
BEGIN
 IF TG_OP = 'INSERT' OR NEW.channel_id IS DISTINCT FROM OLD.channel_id
 OR (NEW.moderation_status IN ('pending','approved') AND NEW.moderation_status IS DISTINCT FROM OLD.moderation_status) THEN
  PERFORM 1 FROM sponsor_channels WHERE id=NEW.channel_id AND archived_at IS NULL FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'channel_unavailable' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP = 'INSERT' OR NEW.tariff_id IS DISTINCT FROM OLD.tariff_id THEN
  PERFORM 1 FROM tariffs WHERE id=NEW.tariff_id AND archived_at IS NULL FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'tariff_unavailable' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.placement_status IN ('scheduled','active') THEN
  PERFORM 1 FROM proxy_nodes WHERE id=NEW.proxy_node_id AND archived_at IS NULL AND status='active' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'node_unavailable' USING ERRCODE='23514'; END IF;
  IF NEW.archived_at IS NOT NULL THEN RAISE EXCEPTION 'campaign_archived' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS campaigns_management_reference_guard ON campaigns;
CREATE TRIGGER campaigns_management_reference_guard BEFORE INSERT OR UPDATE ON campaigns
 FOR EACH ROW EXECUTE FUNCTION pushok_management_reference_guard();
