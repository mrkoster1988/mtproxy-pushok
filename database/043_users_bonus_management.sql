-- Вход сохраняется независимо от отзыва/удаления сессий при изменении роли.
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
UPDATE users u SET last_login_at=s.entered FROM
 (SELECT user_id,max(created_at) AS entered FROM user_sessions GROUP BY user_id) s
 WHERE u.id=s.user_id AND (u.last_login_at IS NULL OR u.last_login_at<s.entered);
CREATE OR REPLACE FUNCTION pushok_remember_login() RETURNS trigger AS $$
BEGIN
 UPDATE users SET last_login_at=greatest(last_login_at,NEW.created_at) WHERE id=NEW.user_id;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS user_session_remember_login ON user_sessions;
CREATE TRIGGER user_session_remember_login AFTER INSERT ON user_sessions
 FOR EACH ROW EXECUTE FUNCTION pushok_remember_login();

-- Удаление прекращает новые активации, не удаляя проводки и историю заказов.
ALTER TABLE wallet_gifts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE growth_offers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
