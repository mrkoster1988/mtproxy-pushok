-- До этого обновления бонусы можно было расходовать во всех рублёвых кошельках.
ALTER TABLE wallet_settings ADD COLUMN IF NOT EXISTS bonus_spending_enabled boolean NOT NULL DEFAULT true;
