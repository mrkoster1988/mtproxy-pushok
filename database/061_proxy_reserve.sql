-- Первый дополнительный экземпляр можно подготовить заранее, без рекламной очереди.
ALTER TABLE proxy_provisioning_settings
 ADD COLUMN IF NOT EXISTS reserve_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE proxy_provisioning_jobs
 ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'demand'
 CHECK(reason IN ('demand','reserve'));
