-- =============================================================================
-- 09-event-geom-trigger.sql
-- Валидация соответствия geometry type ↔ strategy при INSERT/UPDATE в events.
--
-- Инварианты:
--   random           → всегда POINT
--   intersection     → всегда POINT
--   weighted_centroid → всегда POINT
--   street_segment   → всегда LINESTRING
--   single_match     → любой тип (geo-объект может быть точкой или линией)
-- =============================================================================

CREATE OR REPLACE FUNCTION validate_event_geom_strategy()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.strategy IN ('random', 'weighted_centroid', 'intersection')
       AND ST_GeometryType(NEW.geom) != 'ST_Point' THEN
        RAISE EXCEPTION
            'strategy "%" требует POINT-геометрию, получено: %',
            NEW.strategy, ST_GeometryType(NEW.geom);
    END IF;

    IF NEW.strategy = 'street_segment'
       AND ST_GeometryType(NEW.geom) NOT LIKE 'ST_LineString%' THEN
        RAISE EXCEPTION
            'strategy "street_segment" требует LINESTRING-геометрию, получено: %',
            ST_GeometryType(NEW.geom);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_validate_event_geom ON events;

CREATE TRIGGER trg_validate_event_geom
BEFORE INSERT OR UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION validate_event_geom_strategy();
