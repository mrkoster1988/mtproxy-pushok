-- Явный владелец назначения: ручной выбор нельзя определять временем начала транзакции аудита.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM information_schema.columns
   WHERE table_schema='public' AND table_name='campaigns' AND column_name='auto_schedule_managed') THEN
  ALTER TABLE campaigns ADD COLUMN auto_schedule_managed BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE campaigns ADD COLUMN auto_schedule_tag_pending BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE campaigns ADD CONSTRAINT campaigns_auto_tag_pending_check
    CHECK(NOT auto_schedule_tag_pending OR auto_schedule_managed);
  WITH previous AS (
   SELECT c.id,e.event_type,e.details_json FROM campaigns c
   JOIN LATERAL (
    SELECT event_type,details_json FROM audit_events
    WHERE campaign_id=c.id AND event_type IN
     ('placement_auto_scheduled','placement_scheduled','placement_rescheduled','placement_slot_removed')
    ORDER BY created_at DESC,(event_type='placement_auto_scheduled') ASC,id DESC LIMIT 1
   ) e ON true
   WHERE c.placement_status='scheduled' AND NOT c.auto_schedule_paused
     AND e.event_type='placement_auto_scheduled'
     AND e.details_json->>'node_id'=c.proxy_node_id::text
     AND (e.details_json->>'start_at')::timestamptz=c.scheduled_start_at
     AND (e.details_json->>'end_at')::timestamptz=c.scheduled_end_at
  ) UPDATE campaigns c SET auto_schedule_managed=true,
      auto_schedule_tag_pending=COALESCE(p.details_json->>'promotion_prepared'='false',false)
    FROM previous p WHERE c.id=p.id;
 END IF;
END $$;
