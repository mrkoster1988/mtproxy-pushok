CREATE TABLE IF NOT EXISTS flow_push_devices (
    session_id UUID PRIMARY KEY REFERENCES flow_sessions(id) ON DELETE CASCADE,
    registration_token TEXT NOT NULL UNIQUE CHECK(length(registration_token) BETWEEN 16 AND 2048)
);
CREATE TABLE IF NOT EXISTS flow_push_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES flow_push_devices(session_id) ON DELETE CASCADE,
    next_attempt TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS flow_push_due ON flow_push_events(next_attempt);
CREATE OR REPLACE FUNCTION pushok_flow_account_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE recipient UUID;
BEGIN
    IF TG_TABLE_NAME = 'wallet_entries' THEN recipient := NEW.user_id;
    ELSE
        IF NEW.audience <> 'owner' THEN RETURN NEW; END IF;
        recipient := NEW.recipient_user_id;
    END IF;
    INSERT INTO flow_push_events(session_id)
        SELECT s.id FROM flow_sessions s JOIN flow_push_devices d ON d.session_id=s.id
        WHERE s.user_id=recipient AND s.revoked_at IS NULL AND s.expires_at>now();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS flow_push_wallet ON wallet_entries;
CREATE TRIGGER flow_push_wallet AFTER INSERT ON wallet_entries
FOR EACH ROW EXECUTE FUNCTION pushok_flow_account_event();
DROP TRIGGER IF EXISTS flow_push_campaign ON campaign_notice_outbox;
CREATE TRIGGER flow_push_campaign AFTER INSERT ON campaign_notice_outbox
FOR EACH ROW EXECUTE FUNCTION pushok_flow_account_event();
