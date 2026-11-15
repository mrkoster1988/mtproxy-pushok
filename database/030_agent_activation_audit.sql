-- Автоматический запуск имеет системного инициатора, без подмены рекламодателем.
ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_system_actor_check;
ALTER TABLE audit_events ADD CONSTRAINT audit_events_system_actor_check
 -- Старый установщик повторяет 002+ при обновлении. Уже записанные
 -- события из 031 должны пережить этот проход без удаления истории.
 CHECK(actor_user_id IS NOT NULL OR event_type IN ('placement_auto_scheduled','placement_activated_by_agent','placement_completed_by_agent'));
