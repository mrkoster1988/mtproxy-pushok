-- Автоматический запуск имеет системного инициатора, без подмены рекламодателем.
ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_system_actor_check;
ALTER TABLE audit_events ADD CONSTRAINT audit_events_system_actor_check
 CHECK(actor_user_id IS NOT NULL OR event_type IN ('placement_auto_scheduled','placement_activated_by_agent'));
