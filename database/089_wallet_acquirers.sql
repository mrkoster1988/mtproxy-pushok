ALTER TABLE wallet_topups
    DROP CONSTRAINT IF EXISTS wallet_topups_provider_check,
    DROP CONSTRAINT IF EXISTS wallet_topups_check1,
    DROP CONSTRAINT IF EXISTS wallet_topups_provider_currency;
ALTER TABLE wallet_topups
    ADD CONSTRAINT wallet_topups_provider_check CHECK (provider IN ('sbp','telegram_stars','platega','antilopay')),
    ADD CONSTRAINT wallet_topups_provider_currency CHECK (
        (provider IN ('sbp','platega','antilopay') AND currency='RUB')
        OR (provider='telegram_stars' AND currency='XTR')),
    ADD COLUMN IF NOT EXISTS receipt_email TEXT;
CREATE INDEX IF NOT EXISTS wallet_acquirers_due ON wallet_topups(next_check_at,id)
    WHERE provider IN ('platega','antilopay') AND status IN ('pending','succeeded');
