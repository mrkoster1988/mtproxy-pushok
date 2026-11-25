CREATE TABLE IF NOT EXISTS vpn_settings (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), version integer NOT NULL DEFAULT 0,
 policy jsonb NOT NULL DEFAULT '{}'
);
INSERT INTO vpn_settings(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS vpn_recipient_activity (
 user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 checked_at timestamptz NOT NULL DEFAULT now(), last_seen bigint NOT NULL DEFAULT 0,
 ever_used boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS vpn_recipient_checked ON vpn_recipient_activity(checked_at);
CREATE TABLE IF NOT EXISTS vpn_scheduled_notices (
 id bigserial PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind text NOT NULL, anchor text NOT NULL, sent_at bigint, lease text, next_attempt bigint NOT NULL DEFAULT 0,
 blocked boolean NOT NULL DEFAULT false, UNIQUE(user_id,kind)
);
