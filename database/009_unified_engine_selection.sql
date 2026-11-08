-- v0.025. Записываем только ПЛАН движка, а не установленное ПО.
-- Старые узлы остаются с target_engine=NULL. Повторное применение безопасно.
ALTER TABLE proxy_nodes ADD COLUMN IF NOT EXISTS target_engine TEXT;
ALTER TABLE proxy_nodes ADD COLUMN IF NOT EXISTS engine_selection_mode TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE proxy_nodes ADD COLUMN IF NOT EXISTS deployment_profile TEXT NOT NULL DEFAULT 'legacy';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='proxy_nodes_target_engine_check') THEN
    ALTER TABLE proxy_nodes ADD CONSTRAINT proxy_nodes_target_engine_check
      CHECK (target_engine IS NULL OR target_engine IN ('telemt','official_mtproxy','official_docker'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='proxy_nodes_engine_selection_mode_check') THEN
    ALTER TABLE proxy_nodes ADD CONSTRAINT proxy_nodes_engine_selection_mode_check
      CHECK (engine_selection_mode IN ('legacy','manual','auto'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='proxy_nodes_deployment_profile_check') THEN
    ALTER TABLE proxy_nodes ADD CONSTRAINT proxy_nodes_deployment_profile_check
      CHECK (deployment_profile IN ('legacy','native','docker'));
  END IF;
END $$;
