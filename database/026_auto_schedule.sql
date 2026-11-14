-- Ручное снятие слота не должно тут же создавать его повторно.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS auto_schedule_paused BOOLEAN NOT NULL DEFAULT false;
-- Архив скрывает заявку у владельца, но не стирает действующее размещение.
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
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
