-- 07-indexes.sql
-- Phase 2.1: Additional indexes for common query patterns
-- Phase 1.3: Table-specific autovacuum for high-churn events

-- Composite index for time + layer queries (dashboard filtering)
CREATE INDEX IF NOT EXISTS idx_events_time_layer
ON events(event_time DESC, layer);

-- Partial index for photo_url lookups (parser cleanup)
CREATE INDEX IF NOT EXISTS idx_events_photo_url
ON events(photo_url)
WHERE photo_url IS NOT NULL;

-- Composite index for strategy analysis
CREATE INDEX IF NOT EXISTS idx_events_strategy_time
ON events(strategy, event_time DESC);


-- BRIN index for time-series scans (bulk cleanup)
-- Более компактный чем btree для последовательных timestamp
CREATE INDEX IF NOT EXISTS idx_events_time_brin
ON events USING brin (event_time)
WITH (pages_per_range = 32);

-- Index for layer_keywords faster lookups
CREATE INDEX IF NOT EXISTS idx_layer_keywords_gin
ON layer_keywords USING gin (keywords);


