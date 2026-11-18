-- Нижняя граница автоматического старта сохраняется при перезапуске планировщика.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS auto_start_not_before timestamptz;
ALTER TABLE channel_promotion_bindings ADD COLUMN IF NOT EXISTS last_probe_at timestamptz;
