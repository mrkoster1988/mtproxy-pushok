-- Отдельный идентификатор для проверки содержания. Он не подтверждает права владельца.
ALTER TABLE sponsor_channels ADD COLUMN IF NOT EXISTS moderation_chat_id bigint;
CREATE TABLE IF NOT EXISTS channel_moderation_blocks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_channel_id bigint NOT NULL CHECK (telegram_channel_id < 0),
    telegram_url text NOT NULL,
    event_id uuid NOT NULL UNIQUE REFERENCES ai_moderation_events(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lifted_at timestamptz,
    lifted_by uuid REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS active_channel_moderation_block
    ON channel_moderation_blocks(telegram_channel_id) WHERE lifted_at IS NULL;

CREATE OR REPLACE FUNCTION forget_moderation_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.telegram_url IS DISTINCT FROM NEW.telegram_url THEN NEW.moderation_chat_id = NULL; END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER forget_moderation_identity BEFORE UPDATE OF telegram_url ON sponsor_channels
    FOR EACH ROW EXECUTE FUNCTION forget_moderation_identity();

-- Старые записи без ID удерживаем по последнему известному адресу до свежей проверки Telegram.
CREATE OR REPLACE FUNCTION moderation_channel_blocked(ch sponsor_channels) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS(SELECT 1 FROM channel_moderation_blocks b WHERE b.lifted_at IS NULL AND
        CASE WHEN coalesce(ch.telegram_channel_id, ch.moderation_chat_id) IS NULL
             THEN b.telegram_url = ch.telegram_url
             ELSE b.telegram_channel_id = coalesce(ch.telegram_channel_id, ch.moderation_chat_id) END)
$$;

CREATE OR REPLACE FUNCTION check_campaign_channel_block() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE channel sponsor_channels; chat_id bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.channel_id = NEW.channel_id
       AND OLD.moderation_status = NEW.moderation_status AND OLD.placement_status = NEW.placement_status THEN RETURN NEW; END IF;
    IF NEW.moderation_status IN ('pending', 'approved') AND
       (TG_OP = 'INSERT' OR OLD.moderation_status IS DISTINCT FROM NEW.moderation_status
        OR OLD.channel_id IS DISTINCT FROM NEW.channel_id OR NEW.placement_status IN ('scheduled','active')) THEN
        SELECT * INTO channel FROM sponsor_channels WHERE id=NEW.channel_id;
        chat_id := coalesce(channel.telegram_channel_id,channel.moderation_chat_id);
        IF chat_id IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(chat_id);
        END IF;
        IF moderation_channel_blocked(channel) THEN
            RAISE EXCEPTION 'channel_moderation_blocked' USING ERRCODE='PCH01';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER check_campaign_channel_block BEFORE INSERT OR UPDATE OF moderation_status,channel_id,placement_status ON campaigns
    FOR EACH ROW EXECUTE FUNCTION check_campaign_channel_block();
