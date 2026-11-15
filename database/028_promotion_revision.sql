-- Старое подтверждение не доказывает применение изменившегося плана.
-- NULL оставляет старые записи неподтверждёнными до следующего ответа агента.
ALTER TABLE promotion_runtime ADD COLUMN IF NOT EXISTS plan_revision TEXT
 CHECK (plan_revision IS NULL OR plan_revision ~ '^[0-9a-f]{64}$');
