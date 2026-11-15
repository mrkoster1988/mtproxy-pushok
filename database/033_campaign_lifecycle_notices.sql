-- Долговечные уведомления о реальных переходах статуса, а не о наступлении даты.
CREATE TABLE IF NOT EXISTS campaign_notice_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 event_id UUID NOT NULL,
 campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
 recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 audience TEXT NOT NULL CHECK(audience IN ('owner','staff')),
 event_type TEXT NOT NULL CHECK(event_type IN ('started','completed','failed')),
 reason TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 claim_token UUID, lease_until TIMESTAMPTZ,
 delivered_at TIMESTAMPTZ, abandoned_at TIMESTAMPTZ,
 UNIQUE(event_id,recipient_user_id)
);
CREATE INDEX IF NOT EXISTS campaign_notice_due_idx ON campaign_notice_outbox(next_attempt_at,id)
 WHERE delivered_at IS NULL AND abandoned_at IS NULL;
CREATE TABLE IF NOT EXISTS promotion_incidents (
 proxy_node_id UUID PRIMARY KEY REFERENCES proxy_nodes(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 resolved_at TIMESTAMPTZ
);
CREATE OR REPLACE FUNCTION pushok_enqueue_lifecycle(cid UUID,kind TEXT,why TEXT DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE eid UUID := gen_random_uuid();
BEGIN
 INSERT INTO campaign_notice_outbox(event_id,campaign_id,recipient_user_id,audience,event_type,reason)
 SELECT eid,cid,recipient,CASE WHEN bool_or(staff) THEN 'staff' ELSE 'owner' END,kind,why
 FROM (
   SELECT advertiser_user_id AS recipient,FALSE AS staff FROM campaigns WHERE id=cid
   UNION ALL
   SELECT u.id,TRUE FROM bot_operator_subscriptions s JOIN users u ON u.id=s.operator_user_id
   WHERE u.role IN ('admin','operator') AND u.telegram_user_id>0
 ) recipients GROUP BY recipient;
END; $$;
CREATE OR REPLACE FUNCTION pushok_lifecycle_notice_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.placement_status IS DISTINCT FROM OLD.placement_status THEN
  IF NEW.placement_status='active' THEN PERFORM pushok_enqueue_lifecycle(NEW.id,'started'); END IF;
  IF NEW.placement_status='completed' THEN PERFORM pushok_enqueue_lifecycle(NEW.id,'completed'); END IF;
 END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS campaign_lifecycle_notices ON campaigns;
CREATE TRIGGER campaign_lifecycle_notices AFTER UPDATE OF placement_status ON campaigns
 FOR EACH ROW EXECUTE FUNCTION pushok_lifecycle_notice_trigger();
