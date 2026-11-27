ALTER TABLE hall_settings ADD COLUMN IF NOT EXISTS flow_gifts jsonb NOT NULL DEFAULT '{"enabled":false,"minimum":10,"days":[90,30,30]}';
ALTER TABLE hall_settings ADD COLUMN IF NOT EXISTS flow_gifts_since timestamptz NOT NULL DEFAULT now();
CREATE TABLE IF NOT EXISTS hall_flow_gifts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), period_id bigint NOT NULL REFERENCES hall_periods(id),
 user_id uuid NOT NULL REFERENCES users(id), rank int NOT NULL, score bigint NOT NULL,
 days int NOT NULL CHECK(days IN (30,90)), expires bigint, UNIQUE(period_id,user_id)
);
