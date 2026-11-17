-- Телефон никогда не принимается от WebView: только собственный Contact от бота.
CREATE TABLE IF NOT EXISTS verified_profiles (
 user_id UUID NOT NULL REFERENCES users(id),
 phone_number TEXT CHECK(phone_number ~ '^\+[0-9]{7,15}$'),
 phone_verified_at TIMESTAMPTZ,
 telegram_update_id BIGINT,
 telegram_environment TEXT NOT NULL DEFAULT 'production' CHECK(telegram_environment IN ('production','test')),
 PRIMARY KEY(user_id,telegram_environment),
 UNIQUE(telegram_environment,telegram_update_id),
 birth_day SMALLINT,
 birth_month SMALLINT,
 birth_year SMALLINT,
 birthday_source TEXT CHECK(birthday_source IN ('telegram','manual')),
 birthday_opt_in BOOLEAN NOT NULL DEFAULT false,
 consent_version TEXT,
 consent_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK((phone_number IS NULL) = (phone_verified_at IS NULL)),
 CHECK((birth_day IS NULL AND birth_month IS NULL AND birth_year IS NULL AND birthday_source IS NULL)
    OR (birth_day IS NOT NULL AND birth_month IS NOT NULL AND birth_day BETWEEN 1 AND 31 AND birth_month BETWEEN 1 AND 12 AND birthday_source IS NOT NULL
        AND (birth_year IS NULL OR birth_year BETWEEN 1900 AND 2200)))
);
CREATE TABLE IF NOT EXISTS phone_verification_requests (
 user_id UUID NOT NULL REFERENCES users(id),
 requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '10 minutes',
 birthday_opt_in BOOLEAN NOT NULL,
 consent_version TEXT NOT NULL,
 telegram_environment TEXT NOT NULL DEFAULT 'production' CHECK(telegram_environment IN ('production','test')),
 PRIMARY KEY(user_id,telegram_environment)
);
CREATE TABLE IF NOT EXISTS birthday_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 congratulations_enabled BOOLEAN NOT NULL DEFAULT true,
 bonus_amount BIGINT NOT NULL DEFAULT 0 CHECK(bonus_amount BETWEEN 0 AND 1000000),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_by UUID REFERENCES users(id)
);
INSERT INTO birthday_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
-- Одна запись на календарный год: изменение даты рождения не повторяет подарок.
CREATE TABLE IF NOT EXISTS birthday_rewards (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id UUID NOT NULL REFERENCES users(id),
 reward_year INTEGER NOT NULL,
 birthday_date DATE NOT NULL,
 bonus_amount BIGINT NOT NULL DEFAULT 0,
 entry_id UUID UNIQUE REFERENCES wallet_entries(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(user_id,reward_year)
);
CREATE TABLE IF NOT EXISTS birthday_notice_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 reward_id UUID NOT NULL UNIQUE REFERENCES birthday_rewards(id),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 claim_token UUID,
 lease_until TIMESTAMPTZ,
 delivered_at TIMESTAMPTZ,
 abandoned_at TIMESTAMPTZ
);
