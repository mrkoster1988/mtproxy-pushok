-- Сигнал доставляется только после COMMIT. Источником работы остаются строки БД.
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0);
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS retry_at timestamptz;
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS lease_until timestamptz;
ALTER TABLE ai_moderation_checks ADD COLUMN IF NOT EXISTS claim_token uuid;

CREATE OR REPLACE FUNCTION pushok_campaign_work() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.archived_at IS NULL AND NEW.moderation_status='pending' THEN
    IF TG_OP='INSERT' OR OLD.moderation_status IS DISTINCT FROM NEW.moderation_status
       OR OLD.updated_at IS DISTINCT FROM NEW.updated_at THEN
      PERFORM pg_notify('pushok_campaign_work', 'moderation');
    END IF;
  END IF;
  IF NEW.archived_at IS NULL AND NEW.moderation_status='approved'
     AND NEW.payment_status='verified' AND NOT NEW.auto_schedule_paused THEN
    IF TG_OP='INSERT' OR OLD.moderation_status IS DISTINCT FROM NEW.moderation_status
       OR OLD.payment_status IS DISTINCT FROM NEW.payment_status
       OR OLD.auto_schedule_paused IS DISTINCT FROM NEW.auto_schedule_paused
       OR OLD.requested_start_at IS DISTINCT FROM NEW.requested_start_at THEN
      PERFORM pg_notify('pushok_campaign_work', 'schedule');
    END IF;
  END IF;
  -- Освободившийся период может потребоваться другой готовой заявке.
  IF TG_OP='UPDATE' AND OLD.placement_status IN ('scheduled','active')
     AND (NEW.archived_at IS NOT NULL OR NEW.placement_status IN ('completed','cancelled')) THEN
    PERFORM pg_notify('pushok_campaign_work', 'schedule');
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS pushok_campaign_work_trigger ON campaigns;
CREATE TRIGGER pushok_campaign_work_trigger AFTER INSERT OR UPDATE ON campaigns
FOR EACH ROW EXECUTE FUNCTION pushok_campaign_work();
