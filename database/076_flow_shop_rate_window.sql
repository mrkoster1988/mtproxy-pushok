-- Расширение существующего лимитера Flow для одноразового MTProxy -> Shop.
-- Миграция безопасна для действующих запросов: сохранённые счётчики не удаляются,
-- более широкие права доступа не выдаются, боту/Pushok ID ничего не меняется.
-- Повторное применение допускается штатным мигратором.
ALTER TABLE flow_rate_windows
    DROP CONSTRAINT IF EXISTS flow_rate_windows_bucket_check;

ALTER TABLE flow_rate_windows
    ADD CONSTRAINT flow_rate_windows_bucket_check
    CHECK (bucket IN ('device','token','consent','profile','logout','shop'));
