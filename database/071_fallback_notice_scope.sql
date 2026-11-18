-- Сообщения старых версий могли привязать состояние резерва к чужой кампании.
UPDATE campaign_notice_outbox SET abandoned_at=now()
 WHERE event_type='failed' AND reason='default_binding_missing' AND campaign_id IS NOT NULL
   AND delivered_at IS NULL AND abandoned_at IS NULL AND audience='owner';
UPDATE campaign_notice_outbox SET campaign_id=NULL
 WHERE event_type='failed' AND reason='default_binding_missing' AND campaign_id IS NOT NULL
   AND delivered_at IS NULL AND abandoned_at IS NULL AND audience='staff';
