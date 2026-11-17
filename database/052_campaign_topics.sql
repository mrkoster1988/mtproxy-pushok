-- Необязательная тематика — снимок выбора рекламодателя для этой заявки.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS topic text CONSTRAINT campaigns_topic_allowed CHECK (topic IS NULL OR topic IN (
    'technology', 'education', 'business', 'finance', 'marketing', 'career', 'news', 'entertainment', 'gaming', 'culture', 'health', 'beauty', 'sports', 'travel', 'food', 'real_estate', 'auto', 'family', 'pets', 'science', 'industry', 'agriculture', 'shopping', 'lifestyle', 'other'));
