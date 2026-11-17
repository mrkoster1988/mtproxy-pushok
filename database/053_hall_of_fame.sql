-- Итоги и награды фиксируются одной транзакцией; прошлые периоды не пересчитываются.
CREATE TABLE IF NOT EXISTS hall_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 top_size INTEGER NOT NULL DEFAULT 50 CHECK(top_size BETWEEN 3 AND 200),
 rewards JSONB NOT NULL DEFAULT '{}'::jsonb,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO hall_settings DEFAULT VALUES ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS hall_periods (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('week','month','year')),
 starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
 settings JSONB NOT NULL, closed_at TIMESTAMPTZ,
 UNIQUE(kind,starts_at), CHECK(ends_at>starts_at)
);
CREATE TABLE IF NOT EXISTS hall_results (
 period_id BIGINT NOT NULL REFERENCES hall_periods(id),
 metric TEXT NOT NULL, user_id UUID NOT NULL REFERENCES users(id),
 rank INTEGER NOT NULL CHECK(rank>0),score BIGINT NOT NULL CHECK(score>0),
 bonus_amount BIGINT NOT NULL DEFAULT 0 CHECK(bonus_amount>=0),
 entry_id UUID REFERENCES wallet_entries(id),
 PRIMARY KEY(period_id,metric,user_id),UNIQUE(period_id,metric,rank)
);
CREATE INDEX IF NOT EXISTS hall_user_medals ON hall_results(user_id,period_id) WHERE rank<=3;
CREATE INDEX IF NOT EXISTS hall_referrals_date ON growth_referrals(created_at,inviter_user_id);
