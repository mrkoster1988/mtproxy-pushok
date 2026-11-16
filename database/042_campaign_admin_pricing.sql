-- Индивидуальная скидка хранит исходную цену Stars: повторная правка не суммирует скидки.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS base_stars_price_snapshot INTEGER
    CHECK (base_stars_price_snapshot >= 0);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS admin_discount_percent_snapshot INTEGER
    CHECK (admin_discount_percent_snapshot BETWEEN 0 AND 100);
