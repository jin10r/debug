-- 05-role-timeouts.sql
-- Phase 1.4: Role-specific statement timeouts
-- Разные таймауты для разных сервисов:
--   parser: 60s (PostGIS ST_Intersects, process_candidates)
--   core: 30s (простые SELECT, WebSocket уведомления)
--   maintenance: 300s (pg_cron, VACUUM, бэкапы)

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'parser') THEN
        CREATE ROLE parser WITH LOGIN INHERIT;
    END IF;
    EXECUTE 'ALTER ROLE parser SET statement_timeout = ''60s''';
    EXECUTE 'ALTER ROLE parser SET lock_timeout = ''30s''';

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'core') THEN
        CREATE ROLE core WITH LOGIN INHERIT;
    END IF;
    EXECUTE 'ALTER ROLE core SET statement_timeout = ''30s''';
    EXECUTE 'ALTER ROLE core SET lock_timeout = ''15s''';

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maintenance') THEN
        CREATE ROLE maintenance WITH LOGIN INHERIT;
    END IF;
    EXECUTE 'ALTER ROLE maintenance SET statement_timeout = ''300s''';
    EXECUTE 'ALTER ROLE maintenance SET lock_timeout = ''120s''';
END;
$$;

-- Даём доступ к схемам
GRANT USAGE ON SCHEMA public TO parser, core, maintenance;
GRANT ALL ON ALL TABLES IN SCHEMA public TO parser;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO core;
GRANT ALL ON ALL TABLES IN SCHEMA public TO maintenance;

-- Default privileges для новых таблиц
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO parser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO core;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO maintenance;

-- Сессии postgres (админ) получают базовый таймаут
ALTER ROLE postgres SET statement_timeout = '120s';
