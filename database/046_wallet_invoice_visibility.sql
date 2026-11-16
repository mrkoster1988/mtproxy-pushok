-- Удаление из списка не удаляет платёж и не прерывает сверку/возвраты.
ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;
