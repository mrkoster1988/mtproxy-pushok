ALTER TABLE users ADD COLUMN IF NOT EXISTS is_friend boolean NOT NULL DEFAULT false;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('advertiser','moderator','operator','admin'));

CREATE OR REPLACE VIEW moderator_review_queue AS
SELECT c.id,c.advertiser_user_id,ch.owner_user_id,ch.title,ch.telegram_url,a.reason,a.description,
       a.attempted_at
FROM campaigns c
JOIN sponsor_channels ch ON ch.id=c.channel_id
JOIN ai_moderation_checks a ON a.campaign_id=c.id AND a.snapshot=c.updated_at
WHERE c.moderation_status='pending' AND c.archived_at IS NULL AND ch.archived_at IS NULL
  AND a.decision='review' AND a.claim_token IS NULL AND NOT moderation_channel_blocked(ch);
