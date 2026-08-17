-- =============================================================================
-- 15-geo-geom-m.sql
-- Миграция: добавляет геометрию в проекции 3857 (метры) для быстрых
-- пространственных операций (ST_DWithin, ST_Distance, ST_Length).
-- =============================================================================

ALTER TABLE geo
    ADD COLUMN IF NOT EXISTS geom_m GEOMETRY(Geometry, 3857);

CREATE INDEX IF NOT EXISTS idx_geo_geom_m
    ON geo USING gist (geom_m);

CREATE OR REPLACE FUNCTION trg_geo_set_geom_m()
RETURNS TRIGGER AS $$
BEGIN
    NEW.geom_m := CASE
        WHEN NEW.geom IS NULL THEN NULL
        ELSE ST_Transform(ST_MakeValid(NEW.geom), 3857)
    END;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_geo_set_geom_m ON geo;

CREATE TRIGGER trg_geo_set_geom_m
    BEFORE INSERT OR UPDATE OF geom ON geo
    FOR EACH ROW EXECUTE FUNCTION trg_geo_set_geom_m();

UPDATE geo
SET geom_m = ST_Transform(ST_MakeValid(geom), 3857)
WHERE geom IS NOT NULL AND geom_m IS NULL;
