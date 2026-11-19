ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS moderation_changed_at timestamptz;
ALTER TABLE bot_advertiser_notice_outbox ADD COLUMN IF NOT EXISTS operator_decision_reviewed boolean NOT NULL DEFAULT false;

CREATE OR REPLACE FUNCTION track_campaign_moderation_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.moderation_changed_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER track_campaign_moderation_change BEFORE UPDATE OF moderation_status ON campaigns
    FOR EACH ROW WHEN (OLD.moderation_status IS DISTINCT FROM NEW.moderation_status)
    EXECUTE FUNCTION track_campaign_moderation_change();

CREATE OR REPLACE VIEW moderation_operator_reviews AS
SELECT c.id,c.advertiser_user_id,ch.owner_user_id,ch.title,ch.telegram_url,
       coalesce(c.moderator_comment,'') AS reason,c.moderation_status,
       decision.id AS operator_decision_id,decision.created_at AS attempted_at
FROM campaigns c
JOIN sponsor_channels ch ON ch.id=c.channel_id
JOIN LATERAL (
    SELECT id,actor_user_id,created_at,details_json FROM audit_events
    WHERE campaign_id=c.id AND event_type='campaign_moderated'
    ORDER BY created_at DESC,id DESC LIMIT 1
) decision ON decision.created_at>=coalesce(c.moderation_changed_at,c.updated_at)
    AND decision.details_json->>'decision'=c.moderation_status
JOIN users actor ON actor.id=decision.actor_user_id
LEFT JOIN LATERAL (
    SELECT details_json->>'old_role' AS old_role FROM audit_events
    WHERE event_type='team_role_changed' AND details_json->>'telegram_user_id'=actor.telegram_user_id::text
      AND created_at>decision.created_at
    ORDER BY created_at,id LIMIT 1
) role_change ON decision.details_json->>'actor_role' IS NULL
WHERE coalesce(decision.details_json->>'actor_role',role_change.old_role,actor.role)='operator'
  AND c.archived_at IS NULL AND ch.archived_at IS NULL AND NOT moderation_channel_blocked(ch)
  AND c.moderation_status IN ('approved','rejected','changes_requested')
  AND c.placement_status='not_scheduled' AND c.payment_status IN ('not_required','awaiting_payment')
  AND NOT EXISTS(SELECT 1 FROM payment_attempts p WHERE p.campaign_id=c.id)
  AND NOT EXISTS(SELECT 1 FROM payment_proofs p WHERE p.campaign_id=c.id);
