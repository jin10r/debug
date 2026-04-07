-- 01-extensions.sql
-- Минимальные необходимые расширения

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Настройка pg_trgm
ALTER SYSTEM SET pg_trgm.similarity_threshold = 0.3;
SELECT pg_reload_conf();
