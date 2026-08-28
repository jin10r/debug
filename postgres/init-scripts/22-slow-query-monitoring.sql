-- =============================================================================
-- 22-slow-query-monitoring.sql
-- Views and functions for monitoring slow queries via pg_stat_statements.
--
-- Usage:
--   SELECT * FROM v_slow_queries;           -- Top 20 slowest queries
--   SELECT * FROM v_slow_queries(10);       -- Top 10
--   SELECT * FROM v_query_stats_hourly;     -- Hourly query stats
--   SELECT * FROM v_table_io_stats;         -- Table I/O statistics
-- =============================================================================

-- =============================================================================
-- View: v_slow_queries
-- =============================================================================
-- Top N slowest queries with execution stats and query preview.
-- Filters out pg_stat_statements internal queries.

CREATE OR REPLACE VIEW v_slow_queries AS
SELECT
    queryid,
    calls,
    ROUND(mean_exec_time::numeric, 2) AS avg_ms,
    ROUND(stddev_exec_time::numeric, 2) AS stddev_ms,
    ROUND(min_exec_time::numeric, 2) AS min_ms,
    ROUND(max_exec_time::numeric, 2) AS max_ms,
    ROUND(total_exec_time::numeric, 2) AS total_ms,
    ROUND((100 * total_exec_time / SUM(total_exec_time) OVER ())::numeric, 2) AS pct_total,
    rows,
    ROUND((rows / NULLIF(calls, 0))::numeric, 0) AS avg_rows,
    shared_blks_hit,
    shared_blks_read,
    ROUND(
        (100.0 * shared_blks_hit / NULLIF(shared_blks_hit + shared_blks_read, 0))::numeric,
        2
    ) AS cache_hit_pct,
    LEFT(query, 200) AS query_preview
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
  AND query NOT LIKE '%EXPLAIN%'
ORDER BY mean_exec_time DESC;

-- =============================================================================
-- Function: slow_queries(n)
-- =============================================================================
-- Top N slowest queries as a function (for programmatic access).

CREATE OR REPLACE FUNCTION slow_queries(n INTEGER DEFAULT 20)
RETURNS TABLE (
    queryid BIGINT,
    calls BIGINT,
    avg_ms NUMERIC,
    stddev_ms NUMERIC,
    min_ms NUMERIC,
    max_ms NUMERIC,
    total_ms NUMERIC,
    pct_total NUMERIC,
    rows BIGINT,
    avg_rows NUMERIC,
    shared_blks_hit BIGINT,
    shared_blks_read BIGINT,
    cache_hit_pct NUMERIC,
    query_preview TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.queryid,
        s.calls,
        ROUND(s.mean_exec_time::numeric, 2),
        ROUND(s.stddev_exec_time::numeric, 2),
        ROUND(s.min_exec_time::numeric, 2),
        ROUND(s.max_exec_time::numeric, 2),
        ROUND(s.total_exec_time::numeric, 2),
        ROUND((100 * s.total_exec_time / SUM(s.total_exec_time) OVER ())::numeric, 2),
        s.rows,
        ROUND((s.rows / NULLIF(s.calls, 0))::numeric, 0),
        s.shared_blks_hit,
        s.shared_blks_read,
        ROUND(
            (100.0 * s.shared_blks_hit / NULLIF(s.shared_blks_hit + s.shared_blks_read, 0))::numeric,
            2
        ),
        LEFT(s.query, 200)
    FROM pg_stat_statements s
    WHERE s.query NOT LIKE '%pg_stat_statements%'
      AND s.query NOT LIKE '%EXPLAIN%'
    ORDER BY s.mean_exec_time DESC
    LIMIT n;
END;
$$ LANGUAGE plpgsql STABLE;

-- =============================================================================
-- View: v_query_stats_hourly
-- =============================================================================
-- Query statistics aggregated by hour (for trend analysis).
-- Requires pg_stat_statements track = top (or all).

CREATE OR REPLACE VIEW v_query_stats_hourly AS
SELECT
    DATE_TRUNC('hour', stats_reset + (calls * interval '1 millisecond')) AS hour,
    queryid,
    LEFT(query, 100) AS query_preview,
    SUM(calls) AS total_calls,
    ROUND(AVG(mean_exec_time)::numeric, 2) AS avg_ms,
    ROUND(SUM(total_exec_time)::numeric, 2) AS total_ms,
    SUM(rows) AS total_rows
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
GROUP BY
    DATE_TRUNC('hour', stats_reset + (calls * interval '1 millisecond')),
    queryid,
    LEFT(query, 100)
HAVING SUM(calls) > 10
ORDER BY total_ms DESC
LIMIT 50;

-- =============================================================================
-- View: v_table_io_stats
-- =============================================================================
-- Table I/O statistics for identifying hot tables.

CREATE OR REPLACE VIEW v_table_io_stats AS
SELECT
    schemaname,
    relname AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch,
    n_tup_ins AS inserts,
    n_tup_upd AS updates,
    n_tup_del AS deletes,
    n_live_tup AS live_rows,
    n_dead_tup AS dead_rows,
    ROUND(
        (100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0))::numeric,
        2
    ) AS dead_pct,
    pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size,
    last_vacuum,
    last_autovacuum,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE (n_tup_ins + n_tup_upd + n_tup_del) > 0
   OR n_dead_tup > 1000
ORDER BY (n_tup_ins + n_tup_upd + n_tup_del) DESC;

-- =============================================================================
-- View: v_index_usage
-- =============================================================================
-- Index usage statistics for identifying unused/wasteful indexes.

CREATE OR REPLACE VIEW v_index_usage AS
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan AS scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    CASE
        WHEN idx_scan = 0 THEN 'UNUSED'
        WHEN idx_scan < 100 THEN 'LOW'
        ELSE 'OK'
    END AS usage_status
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_relation_size(indexrelid) DESC;

-- =============================================================================
-- View: v_active_queries
-- =============================================================================
-- Currently running queries (non-idle).

CREATE OR REPLACE VIEW v_active_queries AS
SELECT
    pid,
    usename,
    datname,
    state,
    NOW() - query_start AS duration,
    NOW() - xact_start AS xact_duration,
    wait_event_type,
    wait_event,
    LEFT(query, 200) AS query_preview
FROM pg_stat_activity
WHERE state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY duration DESC;

-- =============================================================================
-- View: v_lock_waits
-- =============================================================================
-- Queries waiting for locks (deadlock detection).

CREATE OR REPLACE VIEW v_lock_waits AS
SELECT
    blocked.pid AS blocked_pid,
    blocked.usename AS blocked_user,
    blocking.pid AS blocking_pid,
    blocking.usename AS blocking_user,
    NOW() - blocked.query_start AS waiting_duration,
    LEFT(blocked.query, 150) AS blocked_query,
    LEFT(blocking.query, 150) AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_locks blocked_locks ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocking_locks ON
    blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;

-- =============================================================================
-- Grant access to monitoring role (if exists)
-- =============================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pg_monitor') THEN
        GRANT SELECT ON v_slow_queries TO pg_monitor;
        GRANT SELECT ON v_query_stats_hourly TO pg_monitor;
        GRANT SELECT ON v_table_io_stats TO pg_monitor;
        GRANT SELECT ON v_index_usage TO pg_monitor;
        GRANT SELECT ON v_active_queries TO pg_monitor;
        GRANT SELECT ON v_lock_waits TO pg_monitor;
        GRANT EXECUTE ON FUNCTION slow_queries(INTEGER) TO pg_monitor;
    END IF;
END $$;

-- =============================================================================
-- Log created views
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE 'Created monitoring views:';
    RAISE NOTICE '  - v_slow_queries: Top slowest queries';
    RAISE NOTICE '  - slow_queries(n): Top N slowest queries (function)';
    RAISE NOTICE '  - v_query_stats_hourly: Hourly query stats';
    RAISE NOTICE '  - v_table_io_stats: Table I/O statistics';
    RAISE NOTICE '  - v_index_usage: Index usage statistics';
    RAISE NOTICE '  - v_active_queries: Currently running queries';
    RAISE NOTICE '  - v_lock_waits: Lock contention';
END $$;
