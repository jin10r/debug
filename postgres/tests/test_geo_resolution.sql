-- =============================================================================
-- test_geo_resolution.sql
--
-- Интеграционные тесты для process_candidates() V2.
-- Использование:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f test_geo_resolution.sql
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

CREATE OR REPLACE FUNCTION assert_true(condition BOOLEAN, msg TEXT)
RETURNS VOID AS $$
BEGIN
    IF NOT condition THEN
        RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ── Очистка ─────────────────────────────────────────────────────────────────
DELETE FROM events WHERE description LIKE 'TEST_%';
DELETE FROM geo WHERE names @> ARRAY['TEST_INTERSECTION'] 
   OR names @> ARRAY['TEST_SINGLE']
   OR names @> ARRAY['TEST_STREET_SEGMENT']
   OR names @> ARRAY['TEST_WEIGHTED_CENTROID']
   OR names @> ARRAY['TEST_RANDOM'];

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 1: intersection (перекрёсток)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_INTERSECTION_A'], 'street',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8300, 46.4980),
                     ST_MakePoint(30.8320, 46.4980),
                     ST_MakePoint(30.8320, 46.4990),
                     ST_MakePoint(30.8300, 46.4990),
                     ST_MakePoint(30.8300, 46.4980)
                 ])
             ), 4326)),
            (ARRAY['TEST_INTERSECTION_B'], 'street',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8310, 46.4975),
                     ST_MakePoint(30.8310, 46.4995),
                     ST_MakePoint(30.8325, 46.4995),
                     ST_MakePoint(30.8325, 46.4975),
                     ST_MakePoint(30.8310, 46.4975)
                 ])
             ), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_INTERSECTION_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_INTERSECTION_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.92::float, 0.88::float],
             ARRAY['TEST_INTERSECTION_A', 'TEST_INTERSECTION_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'intersection', 'T1: strategy should be intersection');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T1: geom should be POINT');
    RAISE NOTICE 'TEST 1 (intersection): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 2: single_match (1 кандидат, score 0.92)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _confidence FLOAT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_SINGLE'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, pc.result_confidence
    INTO _strategy, _confidence
    FROM _data,
         process_candidates(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.92::float],
             ARRAY['test_single'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T2: strategy should be single_match');
    PERFORM assert_true(_confidence = 0.92, 'T2: confidence should be 0.92');
    RAISE NOTICE 'TEST 2 (single_match): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 3: street_segment (линия, пересекающая 2 объекта)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_STREET_SEGMENT'], 'street',
             ST_SetSRID(ST_MakeLine(ARRAY[
                 ST_MakePoint(30.8300, 46.4980),
                 ST_MakePoint(30.8320, 46.4980),
                 ST_MakePoint(30.8320, 46.4990)
             ]), 4326)),
            (ARRAY['TEST_CROSS_A'], 'poi',
             ST_SetSRID(ST_MakePolygon(
                 ST_MakeLine(ARRAY[
                     ST_MakePoint(30.8310, 46.4979),
                     ST_MakePoint(30.8311, 46.4979),
                     ST_MakePoint(30.8311, 46.4981),
                     ST_MakePoint(30.8310, 46.4981),
                     ST_MakePoint(30.8310, 46.4979)
                 ])
             ), 4326)),
            (ARRAY['TEST_CROSS_B'], 'poi',
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
            MAX(CASE WHEN names @> ARRAY['TEST_STREET_SEGMENT'] THEN id END) AS id_line,
            MAX(CASE WHEN names @> ARRAY['TEST_CROSS_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_CROSS_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_line, _ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.70::float, 0.70::float],
             ARRAY['TEST_STREET_SEGMENT', 'TEST_CROSS_A', 'TEST_CROSS_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'street_segment', 'T3: strategy should be street_segment');
    PERFORM assert_true(_geom_type = 'ST_LineString', 'T3: geom should be LINESTRING');
    RAISE NOTICE 'TEST 3 (street_segment): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 4: weighted_centroid (2 объекта, scatter <= 1500m)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_WC_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_WC_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83300, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_WC_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_WC_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.85::float],
             ARRAY['TEST_WC_A', 'TEST_WC_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'weighted_centroid', 'T4: strategy should be weighted_centroid');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T4: geom should be POINT');
    RAISE NOTICE 'TEST 4 (weighted_centroid): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 5: random fallback (все кандидаты ниже порога)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _confidence FLOAT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_RANDOM'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, pc.result_confidence
    INTO _strategy, _confidence
    FROM _data,
         process_candidates(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.70::float],
             ARRAY['test_random'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'random', 'T5: strategy should be random (weak candidate)');
    PERFORM assert_true(_confidence = 0.0, 'T5: confidence should be 0.0 for random');
    RAISE NOTICE 'TEST 5 (random fallback): PASSED';
END;
$$;

-- ── Очистка ─────────────────────────────────────────────────────────────────
DELETE FROM events WHERE description LIKE 'TEST_%';
DELETE FROM geo WHERE names @> ARRAY['TEST_INTERSECTION'] 
   OR names @> ARRAY['TEST_SINGLE']
   OR names @> ARRAY['TEST_STREET_SEGMENT']
   OR names @> ARRAY['TEST_WEIGHTED_CENTROID']
   OR names @> ARRAY['TEST_RANDOM'];

DROP FUNCTION assert_equal(ANYELEMENT, ANYELEMENT, TEXT);
DROP FUNCTION assert_true(BOOLEAN, TEXT);

-- Итог
SELECT 'ALL 5 TESTS PASSED' AS status;
