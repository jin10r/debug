-- 12-materialized-views.sql
-- Phase 3.2: Materialized views for common dashboard queries

-- MV: Recent events count by layer (обновляется каждую минуту)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_recent_events_by_layer AS
SELECT
    layer,
    COUNT(*) AS count,
    MAX(event_time) AS latest_time
FROM events
WHERE event_time > NOW() - INTERVAL '1 hour'
GROUP BY layer
WITH NO DATA;

-- Уникальный индекс для одновременного REFRESH
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_recent_events_layer
ON mv_recent_events_by_layer (layer);

-- MV: Recent events for map view (обновляется каждые 30 секунд)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_recent_events_map AS
SELECT
    id,
    event_time,
    description,
    photo_url,
    layer,
    strategy,
    ST_AsGeoJSON(geom) AS geojson,
    matches
FROM events
WHERE event_time > NOW() - INTERVAL '1 hour'
  AND geom IS NOT NULL
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_recent_events_map_id
ON mv_recent_events_map (id);

-- MV: Geo summary for parser cache
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_geo_summary AS
SELECT
    id,
    names,
    ST_AsGeoJSON(geom) AS geojson
FROM geo
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_geo_summary_id
ON mv_geo_summary (id);

-- Функция для обновления всех материализованных view
CREATE OR REPLACE FUNCTION refresh_event_materialized_views()
RETURNS INTEGER AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_recent_events_map;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_recent_events_by_layer;
    RETURN 1;
END;
$$ LANGUAGE plpgsql;

-- Функция для обновления geo MV (реже)
CREATE OR REPLACE FUNCTION refresh_geo_materialized_view()
RETURNS INTEGER AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_geo_summary;
    RETURN 1;
END;
$$ LANGUAGE plpgsql;

-- Первичное заполнение
REFRESH MATERIALIZED VIEW mv_recent_events_by_layer;
REFRESH MATERIALIZED VIEW mv_recent_events_map;
REFRESH MATERIALIZED VIEW mv_geo_summary;

-- pg_cron: events MV каждые 30 секунд
SELECT cron.unschedule('refresh-events-mv')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh-events-mv');
SELECT cron.schedule('refresh-events-mv', '*/30 * * * * *', 'SELECT refresh_event_materialized_views()');

-- pg_cron: geo MV каждые 5 минут
SELECT cron.unschedule('refresh-geo-mv')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh-geo-mv');
SELECT cron.schedule('refresh-geo-mv', '*/5 * * * *', 'SELECT refresh_geo_materialized_view()');
