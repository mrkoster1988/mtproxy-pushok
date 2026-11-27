-- Additive: old events and clients remain compatible.
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 0;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS creation_actor uuid;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS creation_request_id uuid;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS creation_fingerprint text;
CREATE UNIQUE INDEX IF NOT EXISTS contest_events_creation_request ON contest_events(creation_actor,creation_request_id);
