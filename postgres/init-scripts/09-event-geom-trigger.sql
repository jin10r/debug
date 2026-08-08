-- =============================================================================
-- 09-event-geom-trigger.sql
-- Валидация соответствия geometry type ↔ strategy при INSERT/UPDATE в events.
--
-- Инварианты:
--   random           → всегда POINT
--   midpoint         → всегда POINT
--   cluster_centroid → всегда POINT
--   intersection     → может быть POINT (одиночное) или не-POINT (множественное)
--   single_match     → любой тип (geo-объект может быть точкой или линией)
-- =============================================================================

CREATE OR REPLACE FUNCTION validate_event_geom_strategy()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.strategy IN ('random', 'midpoint', 'cluster_centroid')
       AND ST_GeometryType(NEW.geom) != 'ST_Point' THEN
        RAISE EXCEPTION
            'strategy "%" требует POINT-геометрию, получено: %',
            NEW.strategy, ST_GeometryType(NEW.geom);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_event_geom
BEFORE INSERT OR UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION validate_event_geom_strategy();
