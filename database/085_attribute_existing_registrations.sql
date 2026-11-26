-- Date these at assignment time so pre-existing orders keep their original reward rules.
ALTER TABLE growth_settings ADD COLUMN IF NOT EXISTS direct_registrations_imported_at timestamptz;
DO $$
DECLARE added integer;
BEGIN
    PERFORM pg_advisory_xact_lock(736102484);
    IF (SELECT direct_registrations_imported_at IS NOT NULL FROM growth_settings) THEN
        RETURN;
    END IF;

    INSERT INTO growth_direct_registrations(user_id,registered_at,rewards_enabled,reward_mode,
        reward_percent,reward_days,reward_bonus_kopecks)
    SELECT u.id,statement_timestamp(),s.referrals_enabled,s.reward_mode,s.reward_percent,s.reward_days,s.reward_bonus_kopecks
    FROM users u CROSS JOIN growth_settings s
    WHERE u.telegram_user_id>0 AND NOT u.is_owner
        AND NOT EXISTS (SELECT 1 FROM growth_referrals r WHERE r.referred_user_id=u.id)
    ON CONFLICT (user_id) DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    PERFORM pushok_bind_direct_registrations(NULL);
    UPDATE growth_settings SET direct_registrations_imported_at=statement_timestamp();
    INSERT INTO audit_events(actor_user_id,event_type,details_json)
        SELECT id,'direct_registration_backfill_completed',jsonb_build_object('candidates',added)
        FROM users WHERE is_owner AND blocked_at IS NULL;
END; $$;
