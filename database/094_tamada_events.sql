-- Independent event-manager permission; existing roles and events remain intact.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_tamada boolean NOT NULL DEFAULT false;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS archived_at timestamptz;
