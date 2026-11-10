-- v0.048 — цены управляются в панели, без фиктивного курса RUB/XTR.
-- Цена в рублях по умолчанию сохранена (30000 копеек за 86400 секунд).
-- Stars не включаются автоматически: NULL = администратор ещё не определил XTR.
ALTER TABLE tariffs
    ADD COLUMN IF NOT EXISTS stars_price_xtr INTEGER
    CHECK (stars_price_xtr IS NULL OR stars_price_xtr BETWEEN 1 AND 1000000);

-- Заказы используют snapshots в campaigns; старые заявки не пересчитываются.
