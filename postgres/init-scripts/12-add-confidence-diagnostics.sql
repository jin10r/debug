-- =============================================================================
-- 12-add-confidence-diagnostics.sql
-- Миграция: добавляет confidence и geo_diagnostics в events.
-- =============================================================================

ALTER TABLE events 
ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS geo_diagnostics JSONB;

CREATE INDEX IF NOT EXISTS idx_events_confidence_low ON events(confidence) WHERE confidence < 0.7;
CREATE INDEX IF NOT EXISTS idx_events_strategy_confidence ON events(strategy, confidence);
