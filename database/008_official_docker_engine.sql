-- v0.023: третий вариант движка — официальный Docker MTProxy.
-- Существующие значения telemt/official_mtproxy сохраняются без изменений.
ALTER TABLE proxy_sponsor_registrations
    DROP CONSTRAINT IF EXISTS proxy_sponsor_registrations_engine_check;
ALTER TABLE proxy_sponsor_registrations
    ADD CONSTRAINT proxy_sponsor_registrations_engine_check
    CHECK (engine IN ('telemt', 'official_mtproxy', 'official_docker'));
