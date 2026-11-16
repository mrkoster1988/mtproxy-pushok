-- Выключение кошелька не удаляет остатки и не прерывает сверку оплаченных счетов.
CREATE TABLE IF NOT EXISTS wallet_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled BOOLEAN NOT NULL DEFAULT true,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_by UUID REFERENCES users(id)
);
INSERT INTO wallet_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
ALTER TABLE wallet_entries DROP CONSTRAINT IF EXISTS wallet_entries_kind_check;
ALTER TABLE wallet_entries ADD CONSTRAINT wallet_entries_kind_check CHECK(kind IN (
 'topup','payment','refund','manual_credit','manual_debit','reversal','provider_refund',
 'referral_reward','referral_reversal','gift_credit'));
CREATE TABLE IF NOT EXISTS wallet_gifts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 request_id UUID NOT NULL,
 created_by UUID NOT NULL REFERENCES users(id),
 code TEXT NOT NULL UNIQUE CHECK(code ~ '^GIFT-[A-F0-9]{24}$'),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 3 AND 100),
 amount BIGINT NOT NULL CHECK(amount BETWEEN 1 AND 1000000),
 max_uses INTEGER NOT NULL CHECK(max_uses BETWEEN 1 AND 10000),
 expires_at TIMESTAMPTZ NOT NULL,
 active BOOLEAN NOT NULL DEFAULT true,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(created_by,request_id)
);
CREATE TABLE IF NOT EXISTS wallet_gift_redemptions (
 gift_id UUID NOT NULL REFERENCES wallet_gifts(id),
 user_id UUID NOT NULL REFERENCES users(id),
 entry_id UUID NOT NULL UNIQUE REFERENCES wallet_entries(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(gift_id,user_id)
);
