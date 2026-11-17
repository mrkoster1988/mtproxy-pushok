-- Исправление выбора владельца в 056 без изменения уже применённой миграции.
-- Действующий владелец и блокировка прежнего администратора сохраняются.
DO $$ DECLARE old_owner UUID; next_owner UUID; BEGIN
 LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE;
 SELECT id INTO old_owner FROM users WHERE is_owner AND blocked_at IS NOT NULL
   AND id=(SELECT id FROM users WHERE role='admin' ORDER BY created_at,id LIMIT 1);
 IF old_owner IS NOT NULL OR NOT EXISTS(SELECT 1 FROM users WHERE is_owner) THEN
  SELECT id INTO next_owner FROM users WHERE role='admin' AND blocked_at IS NULL
    ORDER BY created_at,id LIMIT 1;
  IF next_owner IS NOT NULL THEN
   UPDATE users SET is_owner=false WHERE id=old_owner;
   UPDATE users SET is_owner=true WHERE id=next_owner;
   INSERT INTO audit_events(actor_user_id,event_type,details_json)
     VALUES(next_owner,'owner_migration_recovered',jsonb_build_object('previous_owner',old_owner,'migration','062'));
  END IF;
 END IF;
END $$;
