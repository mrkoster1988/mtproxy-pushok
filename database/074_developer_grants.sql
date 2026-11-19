-- Владелец установки и назначенные разработчики имеют права разработчика.
ALTER TABLE users ADD COLUMN IF NOT EXISTS developer_granted boolean NOT NULL DEFAULT false
    CHECK (NOT developer_granted OR role = 'admin');
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_developer boolean
    GENERATED ALWAYS AS (is_owner OR developer_granted) STORED;
