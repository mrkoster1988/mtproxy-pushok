-- Бесплатное размещение проходит модерацию, без внешнего платежа.
CREATE OR REPLACE FUNCTION pushok_free_campaign() RETURNS trigger AS $$
BEGIN
  IF NEW.price_kopecks_snapshot = 0 AND NEW.moderation_status = 'approved'
     AND NEW.payment_status IN ('not_required','awaiting_payment') THEN
    NEW.payment_status := 'verified';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pushok_free_campaign_trigger ON campaigns;
CREATE TRIGGER pushok_free_campaign_trigger BEFORE INSERT OR UPDATE ON campaigns
FOR EACH ROW EXECUTE FUNCTION pushok_free_campaign();
