-- PostgreSQL Performance Monitoring Queries
-- Используется для диагностики производительности и оптимизации

-- ========================================
-- 1. Top Slow Queries (pg_stat_statements)
-- ========================================
-- Показывает самые медленные запросы по среднему времени выполнения
SELECT
    calls,
    ROUND(mean_exec_time::numeric, 2) AS avg_ms,
    ROUND(total_exec_time::numeric, 2) AS total_ms,
    ROUND((100 * total_exec_time / SUM(total_exec_time) OVER ())::numeric, 2) AS pct,
    query
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY mean_exec_time DESC
LIMIT 20;

-- ========================================
-- 2. Most Frequent Queries
-- ========================================
-- Показывает наиболее часто выполняемые запросы
SELECT
    calls,
    ROUND(mean_exec_time::numeric, 2) AS avg_ms,
    ROUND(total_exec_time::numeric, 2) AS total_ms,
    query
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY calls DESC
LIMIT 20;

-- ========================================
-- 3. Connection Pool Status
-- ========================================
-- Показывает количество активных/idle соединений по базам
SELECT
    datname,
    state,
    COUNT(*) as connections,
    MAX(EXTRACT(EPOCH FROM (NOW() - state_change))) AS max_age_seconds
FROM pg_stat_activity
WHERE pid != pg_backend_pid()
GROUP BY datname, state
ORDER BY connections DESC;

-- ========================================
-- 4. Long Running Queries
-- ========================================
-- Показывает запросы, выполняющиеся дольше 5 секунд
SELECT
    pid,
    now() - query_start AS duration,
    state,
    LEFT(query, 100) AS query
FROM pg_stat_activity
WHERE state != 'idle'
  AND query_start IS NOT NULL
  AND now() - query_start > interval '5 seconds'
ORDER BY duration DESC;

-- ========================================
-- 5. Lock Contention
-- ========================================
-- Показывает блокировки и ожидающие процессы
SELECT
    blocked_locks.pid AS blocked_pid,
    blocked_activity.usename AS blocked_user,
    blocking_locks.pid AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.query AS blocked_statement,
    blocking_activity.query AS blocking_statement
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks 
    ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;

-- ========================================
-- 6. Table Bloat and Dead Tuples
-- ========================================
-- Показывает мертвые строки и автовакуум статистику
SELECT
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 20;

-- ========================================
-- 7. Index Usage Statistics
-- ========================================
-- Показывает использование индексов (неиспользуемые индексы можно удалить)
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND indexrelname NOT LIKE '%pkey'
ORDER BY pg_relation_size(indexrelid) DESC
LIMIT 20;

-- ========================================
-- 8. Cache Hit Ratio
-- ========================================
-- Показывает эффективность кэша (должно быть >99%)
SELECT
    'index hit rate' AS metric,
    ROUND((sum(idx_blks_hit) / NULLIF(sum(idx_blks_hit + idx_blks_read), 0) * 100)::numeric, 2) AS ratio
FROM pg_statio_user_indexes
UNION ALL
SELECT
    'table hit rate' AS metric,
    ROUND((sum(heap_blks_hit) / NULLIF(sum(heap_blks_hit + heap_blks_read), 0) * 100)::numeric, 2) AS ratio
FROM pg_statio_user_tables;

-- ========================================
-- 9. Partition Statistics (for events table)
-- ========================================
-- Показывает размер и статистику партиций таблицы events
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
    n_live_tup,
    n_dead_tup,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
WHERE tablename LIKE 'events_%'
ORDER BY tablename DESC
LIMIT 50;

-- ========================================
-- 10. Top Tables by Size
-- ========================================
-- Показывает самые большие таблицы
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
    pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) AS table_size,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename) - 
                   pg_relation_size(schemaname||'.'||tablename)) AS indexes_size
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 20;

-- ========================================
-- 11. Replication Lag (if applicable)
-- ========================================
SELECT
    client_addr,
    state,
    sync_state,
    pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn) AS send_lag_bytes,
    pg_wal_lsn_diff(sent_lsn, write_lsn) AS write_lag_bytes,
    pg_wal_lsn_diff(write_lsn, flush_lsn) AS flush_lag_bytes,
    pg_wal_lsn_diff(flush_lsn, replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication;

-- ========================================
-- 12. Sequential Scans on Large Tables
-- ========================================
-- Показывает таблицы с частыми seq scan (возможно нужны индексы)
SELECT
    schemaname,
    tablename,
    seq_scan,
    seq_tup_read,
    idx_scan,
    ROUND(100.0 * seq_tup_read / NULLIF(seq_tup_read + idx_tup_fetch, 0), 2) AS seq_scan_pct,
    pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) AS table_size
FROM pg_stat_user_tables
WHERE seq_scan > 0
  AND pg_relation_size(schemaname||'.'||tablename) > 8192 * 100  -- > 100 pages
ORDER BY seq_scan DESC
LIMIT 20;

-- ========================================
-- 13. Temp Files Usage
-- ========================================
-- Показывает запросы, создающие временные файлы (нужно больше work_mem)
SELECT
    datname,
    temp_files,
    pg_size_pretty(temp_bytes) AS temp_size,
    blk_read_time,
    blk_write_time
FROM pg_stat_database
WHERE temp_files > 0
ORDER BY temp_bytes DESC;

-- ========================================
-- 14. Write Activity by Table
-- ========================================
-- Показывает таблицы с наибольшей активностью записи
SELECT
    schemaname,
    relname,
    n_tup_ins AS inserts,
    n_tup_upd AS updates,
    n_tup_del AS deletes,
    n_tup_hot_upd AS hot_updates,
    ROUND(100.0 * n_tup_hot_upd / NULLIF(n_tup_upd, 0), 2) AS hot_update_pct
FROM pg_stat_user_tables
WHERE n_tup_ins + n_tup_upd + n_tup_del > 0
ORDER BY (n_tup_ins + n_tup_upd + n_tup_del) DESC
LIMIT 20;

-- ========================================
-- 15. Events Partition Health Check
-- ========================================
-- Проверяет, что партиции events созданы на будущие часы
SELECT
    tablename,
    REGEXP_REPLACE(tablename, 'events_', '') AS partition_hour
FROM pg_tables
WHERE tablename LIKE 'events_%'
  AND schemaname = 'public'
ORDER BY tablename DESC
LIMIT 10;
