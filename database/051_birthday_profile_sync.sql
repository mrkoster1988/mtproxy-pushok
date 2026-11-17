-- Повторное чтение доступной даты Telegram отдельно от подтверждения номера.
ALTER TABLE verified_profiles ADD COLUMN IF NOT EXISTS birthday_sync_checked_at TIMESTAMPTZ;
ALTER TABLE verified_profiles ADD COLUMN IF NOT EXISTS birthday_sync_state TEXT
    CHECK(birthday_sync_state IN ('pending','imported','unavailable','failed','manual'));
-- Ручную дату не перезаписываем; удаление пользователем тоже сохраняет этот выбор.
UPDATE verified_profiles SET birthday_sync_state='manual'
    WHERE birthday_source='manual' AND birthday_sync_state IS NULL;
