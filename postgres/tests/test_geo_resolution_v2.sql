-- =============================================================================
-- test_geo_resolution_v2.sql
--
-- Интеграционные тесты для process_candidates_v2().
-- Использование:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f test_geo_resolution_v2.sql
-- =============================================================================

\set ON_ERROR_STOP on

-- ── Вспомогательные функции ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION assert_equal(actual ANYELEMENT, expected ANYELEMENT, msg TEXT)
RETURNS VOID AS $$
BEGIN
    IF actual != expected THEN
        RAISE EXCEPTION 'ASSERTION FAILED: % — expected %, got %', msg, expected, actual;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION assert_true(cond BOOLEAN, msg TEXT)
RETURNS VOID AS $$
BEGIN
    IF NOT cond THEN
        RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ── Очистка ─────────────────────────────────────────────────────────────────
DELETE FROM events WHERE description LIKE 'TEST_V2_%';
DELETE FROM geo WHERE names @> ARRAY['TEST_V2_INTERSECTION']
   OR names @> ARRAY['TEST_V2_SINGLE']
   OR names @> ARRAY['TEST_V2_STREET_SEGMENT']
   OR names @> ARRAY['TEST_V2_WEIGHTED_CENTROID']
   OR names @> ARRAY['TEST_V2_RANDOM_NULL']
   OR names @> ARRAY['TEST_V2_FAR']
   OR names @> ARRAY['TEST_V2_MULTI']
   OR names @> ARRAY['TEST_V2_POLYGON']
   OR names @> ARRAY['TEST_V2_POLYGON2']
   OR names @> ARRAY['TEST_V2_DUP']
   OR names @> ARRAY['TEST_V2_INVALID'];

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 1: single_match (1 кандидат, score 0.92)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
    _confidence DOUBLE PRECISION;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_V2_SINGLE'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom), pc.result_confidence
    INTO _strategy, _geom_type, _confidence
    FROM _data,
         process_candidates_v2(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.92::double precision],
             ARRAY['test_single'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T1: strategy should be single_match');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T1: geom should be POINT');
    PERFORM assert_true(_confidence = 0.92, 'T1: confidence should be 0.92');
    RAISE NOTICE 'TEST 1 (single_match): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 2: intersection (2 точки в радиусе <= 40м, compact cluster)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_INTERSECTION_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_V2_INTERSECTION_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83150, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_INTERSECTION_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_INTERSECTION_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.92::double precision, 0.88::double precision],
             ARRAY['TEST_V2_INTERSECTION_A', 'TEST_V2_INTERSECTION_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'intersection', 'T2: strategy should be intersection');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T2: geom should be POINT');
    RAISE NOTICE 'TEST 2 (intersection): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 3: street_segment (LINESTRING, пересекающий 2+ объекта в 2+ точках)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_STREET_SEGMENT'], 'street',
             ST_SetSRID(ST_MakeLine(ARRAY[
                 ST_MakePoint(30.8300, 46.4980),
                 ST_MakePoint(30.8320, 46.4980),
                 ST_MakePoint(30.8320, 46.4990)
             ]), 4326)),
            (ARRAY['TEST_V2_CROSS_A'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8310, 46.4979),
                     ST_MakePoint(30.8311, 46.4979),
                     ST_MakePoint(30.8311, 46.4981),
                     ST_MakePoint(30.8310, 46.4981),
                     ST_MakePoint(30.8310, 46.4979)
                 ])
             ), 4326)),
            (ARRAY['TEST_V2_CROSS_B'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8319, 46.4979),
                     ST_MakePoint(30.8320, 46.4979),
                     ST_MakePoint(30.8320, 46.4991),
                     ST_MakePoint(30.8319, 46.4991),
                     ST_MakePoint(30.8319, 46.4979)
                 ])
             ), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_STREET_SEGMENT'] THEN id END) AS id_line,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_CROSS_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_CROSS_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_line, _ids.id_a, _ids.id_b],
             ARRAY[0.90::double precision, 0.70::double precision, 0.70::double precision],
             ARRAY['TEST_V2_STREET_SEGMENT', 'TEST_V2_CROSS_A', 'TEST_V2_CROSS_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'street_segment', 'T3: strategy should be street_segment');
    PERFORM assert_true(_geom_type = 'ST_LineString', 'T3: geom should be LINESTRING');
    RAISE NOTICE 'TEST 3 (street_segment): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 4: weighted_centroid (2 объекта, scatter <= 1500m, нет линии)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_WC_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_V2_WC_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83300, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_WC_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_WC_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::double precision, 0.85::double precision],
             ARRAY['TEST_V2_WC_A', 'TEST_V2_WC_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'weighted_centroid', 'T4: strategy should be weighted_centroid');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T4: geom should be POINT');
    RAISE NOTICE 'TEST 4 (weighted_centroid): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 5: random_null (0 кандидатов)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom GEOMETRY;
BEGIN
    SELECT pc.result_strategy, pc.result_geom
    INTO _strategy, _geom
    FROM process_candidates_v2(
             ARRAY[]::INTEGER[],
             ARRAY[]::DOUBLE PRECISION[],
             ARRAY[]::TEXT[],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'random_null', 'T5: strategy should be random_null');
    PERFORM assert_true(_geom IS NULL, 'T5: geom should be NULL');
    RAISE NOTICE 'TEST 5 (random_null): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 6: anti-list guard (сильный кандидат-выброс >2000м → single_match)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_FAR_MAIN'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_V2_FAR_OUTLIER'], 'poi',
             ST_SetSRID(ST_MakePoint(30.90000, 46.60000), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_FAR_MAIN'] THEN id END) AS id_main,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_FAR_OUTLIER'] THEN id END) AS id_out
        FROM _data
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_main, _ids.id_out],
             ARRAY[0.95::double precision, 0.90::double precision],
             ARRAY['TEST_V2_FAR_MAIN', 'TEST_V2_FAR_OUTLIER'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T6: anti-list should force single_match');
    RAISE NOTICE 'TEST 6 (anti-list): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 7: MULTILINESTRING (main line → street_segment)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_MULTI'], 'street',
             ST_SetSRID(ST_Multi(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8300, 46.4980),
                     ST_MakePoint(30.8320, 46.4980)
                 ])
             ), 4326)),
            (ARRAY['TEST_V2_CROSS_MULTI_A'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8310, 46.4979),
                     ST_MakePoint(30.8311, 46.4979),
                     ST_MakePoint(30.8311, 46.4981),
                     ST_MakePoint(30.8310, 46.4981),
                     ST_MakePoint(30.8310, 46.4979)
                 ])
             ), 4326)),
            (ARRAY['TEST_V2_CROSS_MULTI_B'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8319, 46.4979),
                     ST_MakePoint(30.8320, 46.4979),
                     ST_MakePoint(30.8320, 46.4991),
                     ST_MakePoint(30.8319, 46.4991),
                     ST_MakePoint(30.8319, 46.4979)
                 ])
             ), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_MULTI'] THEN id END) AS id_line,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_CROSS_MULTI_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_CROSS_MULTI_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_line, _ids.id_a, _ids.id_b],
             ARRAY[0.90::double precision, 0.70::double precision, 0.70::double precision],
             ARRAY['TEST_V2_MULTI', 'TEST_V2_CROSS_MULTI_A', 'TEST_V2_CROSS_MULTI_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'street_segment', 'T7: strategy should be street_segment');
    PERFORM assert_true(_geom_type = 'ST_LineString', 'T7: geom should be LINESTRING');
    RAISE NOTICE 'TEST 7 (MULTILINESTRING): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 8: POLYGON + line + second POLYGON (line intersects 2 polygons → street_segment)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_POLYGON'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8300, 46.4980),
                     ST_MakePoint(30.8320, 46.4980),
                     ST_MakePoint(30.8320, 46.4990),
                     ST_MakePoint(30.8300, 46.4990),
                     ST_MakePoint(30.8300, 46.4980)
                 ])
             ), 4326)),
            (ARRAY['TEST_V2_POLY_LINE'], 'street',
             ST_SetSRID(ST_MakeLine(ARRAY[
                 ST_MakePoint(30.8300, 46.4980),
                 ST_MakePoint(30.8320, 46.4980)
             ]), 4326)),
            (ARRAY['TEST_V2_POLYGON2'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8319, 46.4979),
                     ST_MakePoint(30.8320, 46.4979),
                     ST_MakePoint(30.8320, 46.4991),
                     ST_MakePoint(30.8319, 46.4991),
                     ST_MakePoint(30.8319, 46.4979)
                 ])
             ), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_POLYGON'] THEN id END) AS id_poly,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_POLY_LINE'] THEN id END) AS id_line,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_POLYGON2'] THEN id END) AS id_poly2
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_poly, _ids.id_line, _ids.id_poly2],
             ARRAY[0.92::double precision, 0.88::double precision, 0.70::double precision],
             ARRAY['TEST_V2_POLYGON', 'TEST_V2_POLY_LINE', 'TEST_V2_POLYGON2'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'street_segment', 'T8: strategy should be street_segment');
    PERFORM assert_true(_geom_type = 'ST_LineString', 'T8: geom should be LINESTRING');
    RAISE NOTICE 'TEST 8 (POLYGON + line + POLYGON): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 9: duplicate geo_id (дедупликация по max score)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
    _confidence DOUBLE PRECISION;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_V2_DUP'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, pc.result_confidence
    INTO _strategy, _confidence
    FROM _data,
         process_candidates_v2(
             ARRAY[(SELECT id FROM _data), (SELECT id FROM _data)],
             ARRAY[0.92::double precision, 0.85::double precision],
             ARRAY['TEST_V2_DUP', 'TEST_V2_DUP'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T9: strategy should be single_match');
    PERFORM assert_true(_confidence = 0.92, 'T9: confidence should be 0.92 (max score dedup)');
    RAISE NOTICE 'TEST 9 (duplicate geo_id): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 10: invalid geometry (geom IS NOT NULL but invalid) — должен быть
--           пропущен благодаря ST_MakeValid, fallback на single_match
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_V2_INVALID'], 'street',
                ST_SetSRID(ST_MakeLine(ARRAY[
                    ST_MakePoint(30.83135, 46.49804),
                    ST_MakePoint(30.83135, 46.49804)
                ]), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _data,
         process_candidates_v2(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.92::double precision],
             ARRAY['TEST_V2_INVALID'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T10: strategy should be single_match');
    RAISE NOTICE 'TEST 10 (invalid geometry): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 11: far candidates scatter > 1500m → single_match (не weighted_centroid)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_FAR_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_V2_FAR_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.90000, 46.60000), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_FAR_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_FAR_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::double precision, 0.85::double precision],
             ARRAY['TEST_V2_FAR_A', 'TEST_V2_FAR_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T11: far candidates should fallback to single_match');
    RAISE NOTICE 'TEST 11 (far candidates): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 12: intersection приоритет над weighted_centroid (линия + 2 пересечения)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_V2_LINE'], 'street',
             ST_SetSRID(ST_MakeLine(ARRAY[
                 ST_MakePoint(30.8300, 46.4980),
                 ST_MakePoint(30.8320, 46.4980),
                 ST_MakePoint(30.8320, 46.4990)
             ]), 4326)),
            (ARRAY['TEST_V2_INT_A'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8310, 46.4979),
                     ST_MakePoint(30.8311, 46.4979),
                     ST_MakePoint(30.8311, 46.4981),
                     ST_MakePoint(30.8310, 46.4981),
                     ST_MakePoint(30.8310, 46.4979)
                 ])
             ), 4326)),
            (ARRAY['TEST_V2_INT_B'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8319, 46.4979),
                     ST_MakePoint(30.8320, 46.4979),
                     ST_MakePoint(30.8320, 46.4991),
                     ST_MakePoint(30.8319, 46.4991),
                     ST_MakePoint(30.8319, 46.4979)
                 ])
             ), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_V2_LINE'] THEN id END) AS id_line,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_INT_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_V2_INT_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _ids,
         process_candidates_v2(
             ARRAY[_ids.id_line, _ids.id_a, _ids.id_b],
             ARRAY[0.90::double precision, 0.70::double precision, 0.70::double precision],
             ARRAY['TEST_V2_LINE', 'TEST_V2_INT_A', 'TEST_V2_INT_B'],
             NULL
         ) pc;

    PERFORM assert_equal(_strategy, 'street_segment', 'T12: street_segment should win over intersection');
    RAISE NOTICE 'TEST 12 (street_segment priority): PASSED';
END;
$$;

-- ── Очистка ─────────────────────────────────────────────────────────────────
DELETE FROM events WHERE description LIKE 'TEST_V2_%';
DELETE FROM geo WHERE names @> ARRAY['TEST_V2_INTERSECTION']
   OR names @> ARRAY['TEST_V2_SINGLE']
   OR names @> ARRAY['TEST_V2_STREET_SEGMENT']
   OR names @> ARRAY['TEST_V2_WEIGHTED_CENTROID']
   OR names @> ARRAY['TEST_V2_RANDOM_NULL']
   OR names @> ARRAY['TEST_V2_FAR']
   OR names @> ARRAY['TEST_V2_MULTI']
   OR names @> ARRAY['TEST_V2_POLYGON']
   OR names @> ARRAY['TEST_V2_POLYGON2']
   OR names @> ARRAY['TEST_V2_DUP']
   OR names @> ARRAY['TEST_V2_INVALID'];

DROP FUNCTION assert_equal(ANYELEMENT, ANYELEMENT, TEXT);
DROP FUNCTION assert_true(BOOLEAN, TEXT);

-- Итог
SELECT 'ALL 12 V2 TESTS PASSED' AS status;
