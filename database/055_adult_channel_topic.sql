-- Добавляем категорию, сохраняя все ранее допустимые тематики и заявки.
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_topic_allowed;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_topic_allowed CHECK (topic IS NULL OR topic IN (
    'technology', 'education', 'business', 'finance', 'marketing', 'career', 'news', 'entertainment', 'gaming', 'culture', 'health', 'beauty', 'sports', 'travel', 'food', 'real_estate', 'auto', 'family', 'pets', 'science', 'industry', 'agriculture', 'shopping', 'lifestyle', 'adult', 'other'));
