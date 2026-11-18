-- Сохраняем факт первого запуска, в том числе после отмены или архивации кампании.
ALTER TABLE users ADD COLUMN IF NOT EXISTS has_launched_ads boolean NOT NULL DEFAULT false;

CREATE OR REPLACE FUNCTION remember_campaign_launch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE users SET has_launched_ads = true WHERE id = NEW.advertiser_user_id AND NOT has_launched_ads;
    RETURN NULL;
END
$$;

CREATE OR REPLACE TRIGGER remember_campaign_launch
    AFTER INSERT OR UPDATE OF placement_status ON campaigns
    FOR EACH ROW WHEN (NEW.placement_status IN ('active', 'completed'))
    EXECUTE FUNCTION remember_campaign_launch();

UPDATE users u SET has_launched_ads = true
WHERE NOT u.has_launched_ads AND EXISTS (
    SELECT 1 FROM campaigns c WHERE c.advertiser_user_id = u.id
    AND (c.placement_status IN ('active','completed') OR EXISTS (
        SELECT 1 FROM audit_events a WHERE a.campaign_id = c.id
        AND a.event_type IN ('placement_activated_manually','placement_activated_by_agent',
                             'placement_completed_manually','placement_completed_by_agent')
    ))
);

CREATE TABLE IF NOT EXISTS developer_profile (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    description text NOT NULL CHECK (length(btrim(description)) BETWEEN 1 AND 400),
    history text NOT NULL CHECK (length(btrim(history)) BETWEEN 1 AND 1600),
    revision bigint NOT NULL DEFAULT 1
);
INSERT INTO developer_profile (description, history) VALUES (
    'puShok разрабатывает рекламный кабинет для размещения спонсорских каналов в Telegram через MTProxy.',
    'Проект начался с заявки на размещение и ручной модерации канала. Затем в кабинете появились оплата, календарь кампаний и статистика. Сегодня агент ИИ помогает проверять заявки, а планировщик подбирает время запуска.'
) ON CONFLICT (singleton) DO NOTHING;
