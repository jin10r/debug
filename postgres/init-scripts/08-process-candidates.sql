-- =============================================================================
-- process_candidates.sql
--
-- Выполняет PostGIS-вычисления по стратегии, определённой SemanticResolver.
-- Если p_strategy не указан (fallback) — авто-определение:
--   0 совпадений  → random
--   1 совпадение  → single_match (полная геометрия объекта)
--   2+ совпадений → intersection → midpoint → single_match (best score)
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_geo_ids           INT[]   DEFAULT NULL,
    p_scores            FLOAT[] DEFAULT NULL,
    p_matched_texts     TEXT[]  DEFAULT NULL,
    p_strategy          VARCHAR(40) DEFAULT NULL
)
RETURNS TABLE(
    result_geom     GEOMETRY,
    result_strategy VARCHAR(40),
    result_matches  JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_geom             GEOMETRY;
    v_strategy         VARCHAR(40);
    v_matches          JSONB;
    v_scores           FLOAT[];
    v_true_count       INT;
    v_true_collected   GEOMETRY;
    v_pseudo_count     INT;
    v_pseudo_collected GEOMETRY;
    v_score_threshold  FLOAT := 0.85;   -- geom_min_score для strong_geoms
    v_pseudo_radius    FLOAT := 150.0;  -- м, макс. дистанция для midpoint
    v_midpoint_types   TEXT[] := ARRAY['street', 'market', 'station', 'park', 'landmark'];
BEGIN
    v_scores := COALESCE(
        p_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_geo_ids, 1), 0)])
    );

    -- ── 0 совпадений: случайная точка ─────────────────────────────────────────
    IF p_geo_ids IS NULL OR array_length(p_geo_ids, 1) = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                30.7233 + 0.09 * (random() - 0.5),
                46.4825 + 0.09 * (random() - 0.5)
            ), 4326),
            'random'::VARCHAR(40),
            '[]'::jsonb;
        RETURN;
    END IF;

    -- ── Формируем matches JSON ────────────────────────────────────────────────
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'geo_id',       s.id,
            'name',         s.names[1],
            'similarity',   u.score,
            'matched_text', u.part
        ) ORDER BY u.score DESC
    ), '[]'::jsonb)
    INTO v_matches
    FROM geo s
    JOIN unnest(
        p_geo_ids,
        v_scores,
        COALESCE(p_matched_texts, ARRAY_FILL(NULL::text, ARRAY[array_length(p_geo_ids, 1)]))
    ) AS u(id, score, part) ON s.id = u.id;

    -- ── 1 совпадение или стратегия single_match ───────────────────────────────
    IF array_length(p_geo_ids, 1) = 1 OR p_strategy = 'single_match' THEN
        SELECT ST_MakeValid(geom) INTO v_geom FROM geo WHERE id = p_geo_ids[1];
        RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        RETURN;
    END IF;

    -- ── Стратегия midpoint (вычисление, только для разрешённых типов) ──────────
    IF p_strategy = 'midpoint' THEN
        WITH
        valid_geoms AS (
            SELECT id, ST_MakeValid(geom) AS geom,
                   ST_Transform(ST_MakeValid(geom), 3857) AS geom_m
            FROM geo
            WHERE id = ANY(p_geo_ids)
              AND geom IS NOT NULL
              AND type = ANY(v_midpoint_types)
        ),
        pairs AS (
            SELECT a.id AS id1, b.id AS id2,
                   a.geom AS geom1, b.geom AS geom2,
                   a.geom_m AS geom_m1, b.geom_m AS geom_m2
            FROM valid_geoms a
            CROSS JOIN valid_geoms b
            WHERE a.id < b.id
              AND ST_DWithin(a.geom_m, b.geom_m, v_pseudo_radius)
        ),
        midpoints AS (
            SELECT ST_LineInterpolatePoint(
                ST_ShortestLine(geom1, geom2), 0.5
            ) AS point
            FROM pairs
        )
        SELECT COUNT(*), ST_Collect(point)
        INTO v_true_count, v_true_collected
        FROM midpoints WHERE point IS NOT NULL;

        IF v_true_count > 0 THEN
            IF v_true_count = 1 THEN
                v_geom := ST_GeometryN(v_true_collected, 1);
            ELSE
                v_geom := ST_Centroid(v_true_collected);
            END IF;
            RETURN QUERY SELECT v_geom, 'midpoint'::VARCHAR(40), v_matches;
            RETURN;
        END IF;

        -- Нет пар в радиусе → fallback на single_match лучшего
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        RETURN;
    END IF;

    -- ── Стратегия intersection или fallback (авто-определение) ─────────────────
    WITH
    unique_geoms AS (
        SELECT DISTINCT ON (geom_hash) id, geom, geom_m
        FROM (
            SELECT
                s.id,
                ST_MakeValid(s.geom)                                   AS geom,
                ST_Transform(ST_MakeValid(s.geom), 3857)               AS geom_m,
                ST_AsText(ST_SnapToGrid(ST_MakeValid(s.geom), 0.0001)) AS geom_hash
            FROM geo s
            WHERE s.id = ANY(p_geo_ids)
              AND s.geom IS NOT NULL
        ) sub
        ORDER BY geom_hash, id
    ),
    strong_geoms AS (
        SELECT ug.id, ug.geom, ug.geom_m
        FROM unique_geoms ug
        WHERE EXISTS (
            SELECT 1 FROM unnest(p_geo_ids, v_scores) AS u(id, score)
            WHERE u.id = ug.id AND u.score >= v_score_threshold
        )
    ),
    intersections AS (
        SELECT ST_PointOnSurface(isect.g) AS point
        FROM strong_geoms a
        CROSS JOIN strong_geoms b
        CROSS JOIN LATERAL (SELECT ST_Intersection(a.geom, b.geom) AS g) isect
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(isect.g)
    ),
    midpoints AS (
        SELECT ST_LineInterpolatePoint(
            ST_ShortestLine(a.geom, b.geom), 0.5
        ) AS point
        FROM strong_geoms a
        CROSS JOIN strong_geoms b
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND NOT ST_Intersects(a.geom, b.geom)
          AND ST_DWithin(a.geom_m, b.geom_m, v_pseudo_radius)
    )
    SELECT
        COUNT(*)        FILTER (WHERE src = 'true'   AND point IS NOT NULL)::INT,
        ST_Collect(point) FILTER (WHERE src = 'true' AND point IS NOT NULL),
        COUNT(*)        FILTER (WHERE src = 'pseudo'  AND point IS NOT NULL)::INT,
        ST_Collect(point) FILTER (WHERE src = 'pseudo' AND point IS NOT NULL)
    INTO v_true_count, v_true_collected, v_pseudo_count, v_pseudo_collected
    FROM (
        SELECT point, 'true'   AS src FROM intersections
        UNION ALL
        SELECT point, 'pseudo' AS src FROM midpoints
    ) combined;

    -- Приоритет 1: истинные пересечения
    IF v_true_count > 0 THEN
        IF v_true_count = 1 THEN
            v_geom     := ST_GeometryN(v_true_collected, 1);
        ELSE
            v_geom     := ST_ConvexHull(v_true_collected);
        END IF;
        v_strategy := 'intersection';

    -- Приоритет 2: midpoints (псевдопересечения)
    ELSIF v_pseudo_count > 0 THEN
        IF v_pseudo_count = 1 THEN
            v_geom     := ST_GeometryN(v_pseudo_collected, 1);
        ELSE
            v_geom     := ST_Centroid(v_pseudo_collected);
        END IF;
        v_strategy := 'midpoint';

    -- Приоритет 3: нет пространственной связи → лучший объект по score
    ELSE
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';
    END IF;

    -- Защитный fallback
    IF v_geom IS NULL THEN
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';
    END IF;

    RETURN QUERY SELECT v_geom, v_strategy, v_matches;
END;
$$;
