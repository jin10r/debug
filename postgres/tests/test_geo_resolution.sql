-- =============================================================================
-- test_geo_resolution.sql
--
-- Интеграционные тесты для process_candidates().
-- Использование:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f test_geo_resolution.sql
-- =============================================================================

BEGIN;

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
   OR names @> ARRAY['TEST_MIDPOINT_A']
   OR names @> ARRAY['TEST_CLUSTER_A']
   OR names @> ARRAY['TEST_PENALTY'];

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
-- TEST 2: midpoint (близкие объекты ~50м, >50m порог)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_MIDPOINT_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_MIDPOINT_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83200, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_MIDPOINT_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_MIDPOINT_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.85::float],
             ARRAY['TEST_MIDPOINT_A', 'TEST_MIDPOINT_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'midpoint', 'T2: strategy should be midpoint (>50m)');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T2: geom should be POINT');
    RAISE NOTICE 'TEST 2 (midpoint >50m): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 2b: false midpoint blocked (<50m, was 32m bug)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_FALSE_MIDPOINT_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_FALSE_MIDPOINT_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83170, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_FALSE_MIDPOINT_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_FALSE_MIDPOINT_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.85::float],
             ARRAY['TEST_FALSE_MIDPOINT_A', 'TEST_FALSE_MIDPOINT_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T2b: strategy should be single_match (too close for midpoint)');
    RAISE NOTICE 'TEST 2b (false midpoint blocked): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 3: cluster_centroid (3 объекта)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_CLUSTER_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83100, 46.49800), 4326)),
            (ARRAY['TEST_CLUSTER_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83120, 46.49820), 4326)),
            (ARRAY['TEST_CLUSTER_C'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83140, 46.49810), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_CLUSTER_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_CLUSTER_B'] THEN id END) AS id_b,
            MAX(CASE WHEN names @> ARRAY['TEST_CLUSTER_C'] THEN id END) AS id_c
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b, _ids.id_c],
             ARRAY[0.93::float, 0.91::float, 0.89::float],
             ARRAY['TEST_CLUSTER_A', 'TEST_CLUSTER_B', 'TEST_CLUSTER_C'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'cluster_centroid', 'T3: strategy should be cluster_centroid');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T3: geom should be POINT');
    RAISE NOTICE 'TEST 3 (cluster_centroid): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 4: penalty (short match "7")
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _confidence FLOAT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_PENALTY'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, pc.result_confidence
    INTO _strategy, _confidence
    FROM _data,
         process_candidates(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.92::float],
             ARRAY['7'],
             30.83135, 46.49804, 0.045
         ) pc;

    -- При одном кандидате порог проверяется по RAW score (0.92 >= 0.85),
    -- penalty применяется только в adjusted_score для гипотез 2+.
    -- Штраф за короткий текст (length < 3) снижает final confidence.
    PERFORM assert_equal(_strategy, 'single_match', 'T4: strategy should be single_match');
    PERFORM assert_true(_confidence = 0.92, 'T4: confidence should be 0.92 (raw score, penalty not applied for 1 candidate)');
    RAISE NOTICE 'TEST 4 (penalty): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 5: Option B threshold relaxation (intersection with weak candidate)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _confidence FLOAT;
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
    SELECT pc.result_strategy, pc.result_confidence
    INTO _strategy, _confidence
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.96::float, 0.82::float],
             ARRAY['TEST_INTERSECTION_A', 'TEST_INTERSECTION_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'intersection', 'T5: strategy should be intersection (Option B relaxation)');
    PERFORM assert_true(_confidence > 0.9, 'T5: confidence should be > 0.9');
    RAISE NOTICE 'TEST 5 (Option B threshold): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 6: weak single_match (score 0.75, floor 0.70)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _confidence FLOAT;
    _diagnostics JSONB;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES (ARRAY['TEST_WEAK_SINGLE'], 'street',
                ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326))
        RETURNING id
    )
    SELECT pc.result_strategy, pc.result_confidence, pc.result_diagnostics
    INTO _strategy, _confidence, _diagnostics
    FROM _data,
         process_candidates(
             ARRAY[(SELECT id FROM _data)],
             ARRAY[0.75::float],
             ARRAY['test_weak'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T6: strategy should be single_match (weak)');
    PERFORM assert_true(_confidence = 0.75, 'T6: confidence should be 0.75');
    PERFORM assert_true((_diagnostics->>'weak_candidate')::BOOLEAN, 'T6: diagnostics should have weak_candidate=true');
    RAISE NOTICE 'TEST 6 (weak single_match): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 7: proximity (200m apart, 150–500m range)
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
    _geom_type TEXT;
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_PROXIMITY_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_PROXIMITY_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83300, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_PROXIMITY_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_PROXIMITY_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy, ST_GeometryType(pc.result_geom)
    INTO _strategy, _geom_type
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.85::float],
             ARRAY['TEST_PROXIMITY_A', 'TEST_PROXIMITY_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'proximity', 'T7: strategy should be proximity (200m)');
    PERFORM assert_true(_geom_type = 'ST_Point', 'T7: geom should be POINT');
    RAISE NOTICE 'TEST 7 (proximity): PASSED';
END;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- TEST 8: very close objects (<50m) should NOT be proximity
-- ══════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    _strategy VARCHAR(40);
BEGIN
    WITH _data AS (
        INSERT INTO geo (names, type, geom)
        VALUES
            (ARRAY['TEST_CLOSE_PROX_A'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)),
            (ARRAY['TEST_CLOSE_PROX_B'], 'poi',
             ST_SetSRID(ST_MakePoint(30.83170, 46.49804), 4326))
        RETURNING id, names
    ),
    _ids AS (
        SELECT
            MAX(CASE WHEN names @> ARRAY['TEST_CLOSE_PROX_A'] THEN id END) AS id_a,
            MAX(CASE WHEN names @> ARRAY['TEST_CLOSE_PROX_B'] THEN id END) AS id_b
        FROM _data
    )
    SELECT pc.result_strategy
    INTO _strategy
    FROM _ids,
         process_candidates(
             ARRAY[_ids.id_a, _ids.id_b],
             ARRAY[0.90::float, 0.85::float],
             ARRAY['TEST_CLOSE_PROX_A', 'TEST_CLOSE_PROX_B'],
             30.83135, 46.49804, 0.045
         ) pc;

    PERFORM assert_equal(_strategy, 'single_match', 'T8: strategy should be single_match (too close for proximity/midpoint)');
    RAISE NOTICE 'TEST 8 (close objects not proximity): PASSED';
END;
$$;

-- ── Очистка ─────────────────────────────────────────────────────────────────
DELETE FROM events WHERE description LIKE 'TEST_%';
DELETE FROM geo WHERE names @> ARRAY['TEST_INTERSECTION'] 
   OR names @> ARRAY['TEST_MIDPOINT_A']
   OR names @> ARRAY['TEST_CLUSTER_A']
   OR names @> ARRAY['TEST_PENALTY']
   OR names @> ARRAY['TEST_FALSE_MIDPOINT_A']
   OR names @> ARRAY['TEST_WEAK_SINGLE']
   OR names @> ARRAY['TEST_PROXIMITY_A']
   OR names @> ARRAY['TEST_CLOSE_PROX_A'];

DROP FUNCTION assert_equal(ANYELEMENT, ANYELEMENT, TEXT);
DROP FUNCTION assert_true(BOOLEAN, TEXT);

COMMIT;

-- Итог
SELECT 'ALL 8 TESTS PASSED' AS status;
