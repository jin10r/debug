-- 01-extensions.sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_cron;
-- Топ-N медленных запросов для аудита производительности.
-- Использование: SELECT calls, total_exec_time, mean_exec_time, query
--                FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
