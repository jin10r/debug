-- =============================================================================
-- 09-event-geom-trigger.sql
-- Валидация соответствия geometry type ↔ strategy при INSERT/UPDATE в events.
--
-- Инварианты:
--   random            → всегда POINT
--   single_intersection → всегда POINT
--   polygon_intersection → никогда не POINT (минимум LineString из 2 точек)
--   single_match       → WARNING если POINT, но matched street ≠ POINT
--                        (не EXCEPTION, чтобы не блокировать вставку: аномалия
--                        logируется для диагностики)
-- =============================================================================

CREATE OR REPLACE FUNCTION validate_event_geom_strategy()
RETURNS TRIGGER AS $$
BEGIN
    -- random и single_intersection обязаны быть POINT
    IF NEW.strategy IN ('random', 'single_intersection')
       AND ST_GeometryType(NEW.geom) != 'ST_Point' THEN
        RAISE EXCEPTION
            'strategy "%" требует POINT-геометрию, получено: %',
            NEW.strategy, ST_GeometryType(NEW.geom);
    END IF;

    -- polygon_intersection не может быть POINT
    IF NEW.strategy = 'polygon_intersection'
       AND ST_GeometryType(NEW.geom) = 'ST_Point' THEN
        RAISE EXCEPTION
            'strategy "polygon_intersection" не может быть POINT';
    END IF;

    -- single_match + POINT с непустыми matches — подозрительно, логируем
    IF NEW.strategy = 'single_match'
       AND ST_GeometryType(NEW.geom) = 'ST_Point'
       AND jsonb_array_length(COALESCE(NEW.matches, '[]'::jsonb)) > 0 THEN
        RAISE WARNING
            'single_match event id=% имеет POINT-геометрию с непустыми matches — '
            'возможно ложное совпадение с остановкой трамвая',
            NEW.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_event_geom
BEFORE INSERT OR UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION validate_event_geom_strategy();
