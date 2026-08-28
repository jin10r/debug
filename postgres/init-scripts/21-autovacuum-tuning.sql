-- =============================================================================
-- 21-autovacuum-tuning.sql
-- Table-specific autovacuum settings for high-churn tables.
--
-- These override global autovacuum_* settings from postgresql.conf.
-- Events table: 60-min TTL, high INSERT rate, hourly partitions.
-- Pending events: queue with status transitions, moderate churn.
-- =============================================================================

-- =============================================================================
-- Events table — high churn (60-min TTL, frequent INSERTs + DELETEs)
-- =============================================================================
-- Events are inserted continuously and deleted hourly (partition drop).
-- JSONB columns (matches, geo_diagnostics) add vacuum overhead.
-- GiST index on geom requires careful vacuum to avoid bloat.

ALTER TABLE events SET (
    -- Vacuum more frequently: 2% of table changed (global: 5%)
    autovacuum_vacuum_scale_factor = 0.02,

    -- Analyze even more frequently: 1% changed (global: 2%)
    autovacuum_analyze_scale_factor = 0.01,

    -- Lower threshold: start vacuum after 200 dead tuples (global: 500)
    autovacuum_vacuum_threshold = 200,

    -- Analyze after 100 changes (global: 250)
    autovacuum_analyze_threshold = 100,

    -- More aggressive cost limit: 2000 (global: 1000)
    -- Allows autovacuum to do more work per cycle
    autovacuum_vacuum_cost_limit = 2000,

    -- Faster cleanup: 1ms delay (global: 2ms, default 20ms)
    autovacuum_vacuum_cost_delay = 1,

    -- Scale factor for truncate: 0.1 (10% dead tuples triggers truncate)
    -- Helps reclaim disk space from dropped partitions
    autovacuum_truncate_scale_factor = 0.1
);

-- =============================================================================
-- Pending events table — queue with status transitions
-- =============================================================================
-- Status changes: pending → processing → done/error
-- Rows are rarely updated twice, but 'done' rows accumulate until cleanup.
-- Moderate INSERT rate from parser, moderate DELETE rate from cleanup.

ALTER TABLE pending_events SET (
    -- Vacuum after 5% changed (same as global, but explicit)
    autovacuum_vacuum_scale_factor = 0.05,

    -- Analyze after 2% changed
    autovacuum_analyze_scale_factor = 0.02,

    -- Lower threshold for queue table
    autovacuum_vacuum_threshold = 300,

    autovacuum_analyze_threshold = 150,

    -- Moderate cost limit
    autovacuum_vacuum_cost_limit = 1500,

    -- 2ms delay (same as global)
    autovacuum_vacuum_cost_delay = 2
);

-- =============================================================================
-- Geo table — low churn, read-heavy
-- =============================================================================
-- Geo objects are rarely updated (manual admin changes).
-- Mostly read by processor for matching. No aggressive vacuum needed.

ALTER TABLE geo SET (
    -- Higher thresholds: geo table is stable
    autovacuum_vacuum_scale_factor = 0.1,
    autovacuum_analyze_scale_factor = 0.05,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_threshold = 500,

    -- Lower priority: don't compete with events vacuum
    autovacuum_vacuum_cost_limit = 500,
    autovacuum_vacuum_cost_delay = 5
);

-- =============================================================================
-- Log autovacuum settings for verification
-- =============================================================================
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT
            schemaname,
            relname,
            autovacuum_vacuum_scale_factor,
            autovacuum_analyze_scale_factor,
            autovacuum_vacuum_threshold,
            autovacuum_analyze_threshold,
            autovacuum_vacuum_cost_limit,
            autovacuum_vacuum_cost_delay
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.reltuples > 0
          AND autovacuum_vacuum_scale_factor IS NOT NULL
        ORDER BY relname
    LOOP
        RAISE NOTICE 'Autovacuum for %: scale_factor=%, threshold=%, cost_limit=%',
            r.relname,
            r.autovacuum_vacuum_scale_factor,
            r.autovacuum_vacuum_threshold,
            r.autovacuum_vacuum_cost_limit;
    END LOOP;
END;
$$;
