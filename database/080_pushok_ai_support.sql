UPDATE service_accounts SET name='Pushok ИИ' WHERE id='ai-agent';

CREATE TABLE IF NOT EXISTS support_conversations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id bigint NOT NULL CHECK (telegram_id > 0),
    category text NOT NULL CHECK (category IN ('question','problem','idea')),
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open','waiting_staff','closed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_conversation_owner ON support_conversations(telegram_id,id DESC);
CREATE TABLE IF NOT EXISTS support_chat_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint NOT NULL REFERENCES support_conversations(id) ON DELETE CASCADE,
    sender text NOT NULL CHECK (sender IN ('user','ai-agent','staff')),
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS support_chat_intents (
    telegram_id bigint NOT NULL,
    request_key text NOT NULL,
    conversation_id bigint REFERENCES support_conversations(id),
    reply text,
    PRIMARY KEY(telegram_id,request_key)
);
CREATE TABLE IF NOT EXISTS support_bot_contacts (
    telegram_id bigint PRIMARY KEY, current_case bigint REFERENCES support_conversations(id)
);
CREATE TABLE IF NOT EXISTS support_bot_outbox (
    message_id bigint PRIMARY KEY REFERENCES support_chat_messages(id), telegram_id bigint NOT NULL,
    delivered boolean NOT NULL DEFAULT false, retry_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_bot_outbox_due ON support_bot_outbox(retry_at) WHERE NOT delivered;
