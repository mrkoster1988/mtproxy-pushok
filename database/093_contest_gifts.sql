CREATE TABLE IF NOT EXISTS contest_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organizer bigint NOT NULL CHECK(organizer>0),
 title text NOT NULL, sender text NOT NULL, greeting text NOT NULL DEFAULT '',
 month_limit integer NOT NULL CHECK(month_limit>=0), quarter_limit integer NOT NULL CHECK(quarter_limit>=0),
 created timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS contest_prizes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), event_id uuid NOT NULL REFERENCES contest_events(id),
 request_id uuid NOT NULL, days integer NOT NULL CHECK(days IN (30,90)), recipient bigint CHECK(recipient>0),
 winner uuid REFERENCES users(id), revoked boolean NOT NULL DEFAULT false, expires bigint,
 created timestamptz NOT NULL DEFAULT now(), UNIQUE(event_id,request_id)
);
CREATE INDEX IF NOT EXISTS contest_events_owner ON contest_events(organizer);
CREATE INDEX IF NOT EXISTS contest_prizes_winner ON contest_prizes(winner);
