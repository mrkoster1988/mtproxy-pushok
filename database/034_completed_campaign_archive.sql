-- Завершение сохраняет историю, оплату и отчёты, убирая заявку из рабочих списков.
CREATE OR REPLACE FUNCTION pushok_archive_completed_campaign() RETURNS trigger AS $$
BEGIN
 IF NEW.placement_status='completed' AND OLD.placement_status IS DISTINCT FROM 'completed'
    AND NEW.archived_at IS NULL THEN
  NEW.archived_at := now();
  NEW.archived_state := jsonb_build_object('placement_status','completed','reason','completed');
  NEW.auto_schedule_paused := true;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS campaigns_auto_archive_completed ON campaigns;
CREATE TRIGGER campaigns_auto_archive_completed BEFORE UPDATE OF placement_status ON campaigns
 FOR EACH ROW EXECUTE FUNCTION pushok_archive_completed_campaign();

-- Однократно переносим завершённые ранее кампании; статусы не меняются,
-- уведомления о завершении повторно не создаются.
UPDATE campaigns SET archived_at=now(), auto_schedule_paused=true,
 archived_state=jsonb_build_object('placement_status','completed','reason','completed')
 WHERE placement_status='completed' AND archived_at IS NULL;
