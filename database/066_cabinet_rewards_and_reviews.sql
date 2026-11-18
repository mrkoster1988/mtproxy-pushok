ALTER TABLE growth_settings ADD COLUMN IF NOT EXISTS registration_bonus_kopecks integer
    NOT NULL DEFAULT 0 CHECK (registration_bonus_kopecks BETWEEN 0 AND 1000000);

CREATE OR REPLACE FUNCTION pushok_registration_bonus(uid uuid, amount integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE current_balance bigint;
BEGIN
    IF amount <= 0 THEN RETURN; END IF;
    INSERT INTO wallet_accounts(user_id,currency) VALUES(uid,'BONUS') ON CONFLICT DO NOTHING;
    SELECT balance INTO STRICT current_balance FROM wallet_accounts
        WHERE user_id=uid AND currency='BONUS' FOR UPDATE;
    INSERT INTO wallet_entries(user_id,currency,delta,balance_after,kind,operation_key,reason)
        VALUES(uid,'BONUS',amount,current_balance+amount,'gift_credit','registration:'||uid,'Бонус за регистрацию')
        ON CONFLICT(operation_key) DO NOTHING;
END;
$$;

CREATE OR REPLACE FUNCTION pushok_registered_user() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE amount integer;
BEGIN
    SELECT registration_bonus_kopecks INTO STRICT amount FROM growth_settings FOR SHARE;
    PERFORM pushok_registration_bonus(NEW.id, amount);
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS registration_bonus ON users;
CREATE TRIGGER registration_bonus AFTER INSERT ON users FOR EACH ROW WHEN (NEW.telegram_user_id > 0)
    EXECUTE FUNCTION pushok_registered_user();

-- Выплата относится к пользователю даже после исправления или удаления приглашения.
ALTER TABLE growth_wallet_rewards DROP CONSTRAINT IF EXISTS growth_wallet_rewards_referred_user_id_fkey;
ALTER TABLE growth_wallet_rewards ADD CONSTRAINT growth_wallet_rewards_referred_user_id_fkey
    FOREIGN KEY(referred_user_id) REFERENCES users(id);
ALTER TABLE growth_offers DROP CONSTRAINT IF EXISTS growth_offers_reward_for_user_id_fkey;
ALTER TABLE growth_offers ADD CONSTRAINT growth_offers_reward_for_user_id_fkey
    FOREIGN KEY(reward_for_user_id) REFERENCES users(id);

UPDATE hall_settings SET rewards=rewards-'week' WHERE rewards ? 'week';

CREATE INDEX IF NOT EXISTS ai_event_campaign_time ON ai_moderation_events(campaign_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS campaign_manual_decision_time ON audit_events(campaign_id,created_at DESC)
    WHERE event_type='campaign_moderated';

CREATE OR REPLACE VIEW campaign_agent_reviews AS
SELECT c.id AS campaign_id, coalesce(NULLIF(ac.description,''),NULLIF(ev.description,'')) AS ai_description,
       coalesce(ac.attempted_at,ev.created_at) AS ai_checked_at,
       coalesce(c.moderation_status='rejected' AND c.archived_at IS NULL AND ev.decision='rejected'
          AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.campaign_id=c.id
              AND a.event_type='campaign_moderated' AND a.created_at>=ev.created_at),false) AS ai_rejected
FROM campaigns c
LEFT JOIN ai_moderation_checks ac ON ac.campaign_id=c.id
    AND ac.reason<>'' AND (c.moderation_status<>'pending' OR ac.snapshot=c.updated_at)
LEFT JOIN LATERAL (SELECT decision,created_at,description FROM ai_moderation_events
    WHERE campaign_id=c.id AND decision IN ('approved','rejected')
    ORDER BY created_at DESC,id DESC LIMIT 1) ev ON c.moderation_status IN ('approved','rejected');
