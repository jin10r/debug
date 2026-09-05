-- 01-extensions.sql
-- Extensions required by Survival Map.
-- Order matters: pg_cron must be present before pg_cron jobs are scheduled.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
