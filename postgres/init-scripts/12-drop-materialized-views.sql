-- 12-drop-materialized-views.sql
-- Удаление неиспользуемых materialized views и их cron-задач.
--
-- MV (mv_recent_events_*, mv_geo_summary) не используются ни в одном запросе:
--   • API читает живые таблицы events/geo с in-memory кэшем (TTL 30-60с) + ETag;
--   • REFRESH CONCURRENTLY каждые 30с — фоновая нагрузка на high-churn таблицу
--     (TTL событий 60 мин) без какой-либо пользы;
--   • 30-секундная устарелость MV конфликтовала бы с real-time WebSocket
--     доставкой (incremental updates, since_timestamp catch-up).
--
-- ⚠️ Для уже существующих томов: init-скрипты выполняются только при первом
-- создании volume, поэтому на действующей БД выполните вручную:
--   psql -U postgres -d postgres -c "DROP MATERIALIZED VIEW IF EXISTS mv_recent_events_by_layer, mv_recent_events_map, mv_geo_summary; SELECT cron.unschedule('refresh-events-mv'); SELECT cron.unschedule('refresh-geo-mv');"
-- (уникальные индексы MV удаляются вместе с самими MV).

DROP MATERIALIZED VIEW IF EXISTS mv_recent_events_by_layer;
DROP MATERIALIZED VIEW IF EXISTS mv_recent_events_map;
DROP MATERIALIZED VIEW IF EXISTS mv_geo_summary;

DROP FUNCTION IF EXISTS refresh_event_materialized_views();
DROP FUNCTION IF EXISTS refresh_geo_materialized_view();

SELECT cron.unschedule('refresh-events-mv')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh-events-mv');

SELECT cron.unschedule('refresh-geo-mv')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh-geo-mv');
