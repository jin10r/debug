-- =============================================================================
-- backfill_geo_resolution.sql
--
-- Recomputes geometry for events that were migrated from old strategies
-- (midpoint, proximity, cluster_centroid) to weighted_centroid.
-- Old geometry is semantically different from new weighted_centroid,
-- so we must re-run process_candidates() with the same candidate data.
--
-- Usage:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f backfill_geo_resolution.sql
-- =============================================================================

\set ON_ERROR_STOP on

-- ── Dry-run: show what will be updated ──────────────────────────────────────
SELECT 
    e.id,
    e.strategy AS old_strategy,
    e.geo_diagnostics->>'type' AS old_diagnostic_type,
    ST_GeometryType(e.geom) AS old_geom_type,
    e.matches::text AS matches
FROM events e
WHERE e.geo_diagnostics->>'type' IN ('midpoint', 'proximity', 'cluster_centroid')
ORDER BY e.id
LIMIT 20;

-- Count events to backfill
SELECT COUNT(*) AS events_to_backfill
FROM events
WHERE geo_diagnostics->>'type' IN ('midpoint', 'proximity', 'cluster_centroid');

-- ══════════════════════════════════════════════════════════════════════════════
-- ACTUAL BACKFILL
-- ══════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    v_updated_count INT := 0;
    v_error_count INT := 0;
    event_rec RECORD;
BEGIN
    FOR event_rec IN 
        SELECT id, matches, geo_diagnostics
        FROM events
        WHERE geo_diagnostics->>'type' IN ('midpoint', 'proximity', 'cluster_centroid')
    LOOP
        BEGIN
            -- Reconstruct candidate arrays from matches JSONB
            -- matches format: [{"geo_id": X, "similarity": Y, "matched_text": "Z"}, ...]
            UPDATE events e
            SET 
                geom = pc.result_geom,
                strategy = pc.result_strategy,
                confidence = pc.result_confidence,
                geo_diagnostics = jsonb_set(
                    COALESCE(e.geo_diagnostics, '{}'::jsonb),
                    '{backfilled}',
                    'true'::jsonb
                )
            FROM process_candidates(
                ARRAY(SELECT (m->>'geo_id')::int FROM jsonb_array_elements(event_rec.matches) m),
                ARRAY(SELECT (m->>'similarity')::float FROM jsonb_array_elements(event_rec.matches) m),
                ARRAY(SELECT m->>'matched_text' FROM jsonb_array_elements(event_rec.matches) m),
                30.83135, 46.49804, 0.045
            ) pc
            WHERE e.id = event_rec.id;
            
            v_updated_count := v_updated_count + 1;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'Failed to backfill event %: %', event_rec.id, SQLERRM;
                v_error_count := v_error_count + 1;
        END;
    END LOOP;
    
    RAISE NOTICE 'Backfill complete: % events updated, % errors', v_updated_count, v_error_count;
END;
$$;

-- ── Verification ─────────────────────────────────────────────────────────────
SELECT 
    strategy,
    COUNT(*) AS count
FROM events
WHERE geo_diagnostics ? 'backfilled'
GROUP BY strategy
ORDER BY strategy;

SELECT 
    strategy,
    COUNT(*) AS count
FROM events
WHERE geo_diagnostics->>'type' IN ('midpoint', 'proximity', 'cluster_centroid')
GROUP BY strategy
ORDER BY strategy;

-- Sample of backfilled events
SELECT 
    id,
    strategy,
    ST_GeometryType(geom) AS geom_type,
    confidence,
    geo_diagnostics->>'type' AS diagnostic_type,
    geo_diagnostics->>'backfilled' AS backfilled
FROM events
WHERE geo_diagnostics ? 'backfilled'
LIMIT 10;
