-- Посещения кабинета: только авторизованные пользователи, без IP и токенов сессий.
CREATE TABLE IF NOT EXISTS app_visits (
 user_id UUID NOT NULL REFERENCES users(id), visit_id UUID NOT NULL,
 opened_at TIMESTAMPTZ NOT NULL DEFAULT now(), seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 surface TEXT NOT NULL CHECK(surface IN ('web','telegram')), PRIMARY KEY(user_id,visit_id)
);
CREATE INDEX IF NOT EXISTS app_visits_seen ON app_visits(seen_at DESC,user_id);
-- Сохраняем известные прошлые входы; удалённые сессии восстановить нельзя.
INSERT INTO app_visits(user_id,visit_id,opened_at,seen_at,surface)
 SELECT user_id,id,created_at,created_at,login_surface FROM user_sessions ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS growth_coupon_shares (
 offer_id UUID PRIMARY KEY REFERENCES growth_offers(id), token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 owner_id UUID NOT NULL REFERENCES users(id), claimed_by UUID REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), claimed_at TIMESTAMPTZ
);
