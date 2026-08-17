-- =============================================================================
-- process_candidates.sql
--
-- PostGIS as sole geometry arbiter. No strategy parameter from Python.
-- Generates hypotheses internally: single_match, intersection, street_segment,
-- weighted_centroid. Returns confidence and diagnostics.
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_geo_ids           INT[]   DEFAULT NULL,
    p_scores            FLOAT[] DEFAULT NULL,
    p_matched_texts     TEXT[]  DEFAULT NULL,
    p_center_lon        FLOAT   DEFAULT 30.83135,
    p_center_lat        FLOAT   DEFAULT 46.49804,
    p_radius            FLOAT   DEFAULT 0.045
)
RETURNS TABLE(
    result_geom       GEOMETRY,
    result_strategy   VARCHAR(40),
    result_matches    JSONB,
    result_confidence FLOAT,
    result_diagnostics JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_geom              GEOMETRY;
    v_strategy          VARCHAR(40);
    v_matches           JSONB;
    v_confidence        FLOAT;
    v_diagnostics       JSONB;
    v_scores           FLOAT[];
    v_filtered_ids     INT[];
    v_filtered_scores  FLOAT[];
    v_filtered_texts   TEXT[];
    v_candidate_count   INT;
    v_score_threshold   FLOAT := 0.85;
    v_cluster_radius_m  FLOAT := 500.0;
    v_wc_max_scatter_m  FLOAT := 1500.0;
    v_ss_max_segment_m  FLOAT := 2000.0;
BEGIN
    v_scores := COALESCE(
        p_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_geo_ids, 1), 0)])
    );

    -- ── 0 совпадений: случайная точка ─────────────────────────────────────────
    IF p_geo_ids IS NULL OR array_length(p_geo_ids, 1) = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
            ), 4326),
            'random'::VARCHAR(40),
            '[]'::jsonb,
            0.0::FLOAT,
            jsonb_build_object('reason', 'no_candidates');
        RETURN;
    END IF;

    v_candidate_count := array_length(p_geo_ids, 1);

    -- ── Фильтрация кандидатов по району ───────────────────────────────────────
    WITH
    raw_candidates AS (
        SELECT s.id, s.type, s.geom, u.score, u.matched_text
        FROM geo s
        JOIN unnest(
            p_geo_ids,
            v_scores,
            COALESCE(p_matched_texts, ARRAY_FILL(NULL::text, ARRAY[v_candidate_count]))
        ) AS u(id, score, matched_text) ON s.id = u.id
    ),
    district_filter AS (
        SELECT geom FROM raw_candidates WHERE type = 'district' LIMIT 1
    ),
    filtered_by_district AS (
        SELECT rc.*
        FROM raw_candidates rc
        LEFT JOIN district_filter df ON TRUE
        WHERE df.geom IS NULL
           OR ST_Within(ST_MakeValid(rc.geom), ST_MakeValid(df.geom))
    ),
    final_candidates AS (
        SELECT * FROM filtered_by_district WHERE type != 'district'
    )
    SELECT COALESCE(array_agg(id ORDER BY score DESC), ARRAY[]::INT[]),
           COALESCE(array_agg(score ORDER BY score DESC), ARRAY[]::FLOAT[]),
           COALESCE(array_agg(matched_text ORDER BY score DESC), ARRAY[]::TEXT[])
    INTO v_filtered_ids, v_filtered_scores, v_filtered_texts
    FROM final_candidates;

    -- ── 0 кандидатов после фильтрации: случайная точка ────────────────────────
    IF COALESCE(array_length(v_filtered_ids, 1), 0) = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
            ), 4326),
            'random'::VARCHAR(40),
            '[]'::jsonb,
            0.0::FLOAT,
            jsonb_build_object('reason', 'no_candidates_after_filter');
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
        COALESCE(p_matched_texts, ARRAY_FILL(NULL::text, ARRAY[v_candidate_count]))
    ) AS u(id, score, part) ON s.id = u.id
    WHERE s.id = ANY(v_filtered_ids);

    -- ── 1 совпадение → single_match ───────────────────────────────────────────
    IF array_length(v_filtered_ids, 1) = 1 THEN
        IF v_filtered_scores[1] >= v_score_threshold THEN
            SELECT ST_MakeValid(geom) INTO v_geom FROM geo WHERE id = v_filtered_ids[1];
            v_strategy := 'single_match';
            v_confidence := v_filtered_scores[1];
            v_diagnostics := jsonb_build_object(
                'type', 'single_match',
                'geo_id', v_filtered_ids[1],
                'score', v_filtered_scores[1]
            );
            RETURN QUERY SELECT v_geom, v_strategy, v_matches, v_confidence, v_diagnostics;
        ELSE
            RETURN QUERY SELECT
                ST_SetSRID(ST_MakePoint(
                    p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                    p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
                ), 4326),
                'random'::VARCHAR(40),
                '[]'::jsonb,
                0.0::FLOAT,
                jsonb_build_object('reason', 'weak_single_candidate', 'score', v_filtered_scores[1]);
            RETURN;
        END IF;
    END IF;

    -- ── Генерация гипотез для 2+ кандидатов ──────────────────────────────────
    WITH
    -- Загрузка геометрий с adjusted_score (штраф за короткие совпадения)
    raw_candidates AS (
        SELECT 
            s.id,
            s.type,
            ST_MakeValid(s.geom) AS geom,
            ST_Transform(ST_MakeValid(s.geom), 3857) AS geom_m,
            u.score,
            u.matched_text,
            u.score * (
                CASE 
                    WHEN length(COALESCE(u.matched_text, '')) < 3 THEN 0.7
                    WHEN u.matched_text ~ '^\d+$' THEN 0.6
                    ELSE 1.0
                END
            ) AS adjusted_score
        FROM geo s
        JOIN unnest(
            p_geo_ids,
            v_scores,
            COALESCE(p_matched_texts, ARRAY_FILL(NULL::text, ARRAY[v_candidate_count]))
        ) AS u(id, score, matched_text) ON s.id = u.id
        WHERE s.id = ANY(v_filtered_ids)
          AND s.geom IS NOT NULL
    ),
    -- Дедупликация по геометрии
    deduplicated AS (
        SELECT DISTINCT ON (geom_hash)
            id, type, geom, geom_m, adjusted_score, matched_text
        FROM (
            SELECT *,
                   ST_AsText(ST_SnapToGrid(geom, 0.0001)) AS geom_hash
            FROM raw_candidates
        ) sub
        ORDER BY geom_hash, adjusted_score DESC
    ),
    candidates AS (
        SELECT * FROM deduplicated
    ),

    -- H1: single_match для каждого кандидата (weight 0.4)
    hypothesis_single AS (
        SELECT 
            'single_match'::VARCHAR(40) AS strategy,
            c.geom AS geom,
            c.adjusted_score * 0.4 AS total_score,
            jsonb_build_object(
                'type', 'single_match',
                'geo_id', c.id,
                'score', c.adjusted_score
            ) AS diagnostics
        FROM candidates c
        WHERE c.adjusted_score >= v_score_threshold
    ),

    -- H2: intersection для всех пар (weight 1.0)
    hypothesis_intersection AS (
        SELECT 
            'intersection'::VARCHAR(40) AS strategy,
            CASE 
                WHEN GeometryType(isect.g) IN ('POINT', 'MULTIPOINT')
                    THEN ST_PointOnSurface(isect.g)
                WHEN GeometryType(isect.g) IN ('LINESTRING', 'MULTILINESTRING')
                    THEN ST_LineInterpolatePoint(ST_CollectionExtract(isect.g, 2), 0.5)
                ELSE ST_PointOnSurface(isect.g)
            END AS geom,
            (2 * a.adjusted_score * b.adjusted_score / 
             (a.adjusted_score + b.adjusted_score + 0.001)) * 1.0 AS total_score,
            jsonb_build_object(
                'type', 'intersection',
                'geo_ids', ARRAY[a.id, b.id],
                'distance_m', 0
            ) AS diagnostics
        FROM candidates a
        CROSS JOIN candidates b
        CROSS JOIN LATERAL (SELECT ST_Intersection(a.geom, b.geom) AS g) isect
        WHERE a.id < b.id
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(isect.g)
          AND (
              (a.adjusted_score >= v_score_threshold AND b.adjusted_score >= v_score_threshold)
              OR (a.adjusted_score >= 0.95 AND b.adjusted_score >= 0.80)
              OR (a.adjusted_score >= 0.80 AND b.adjusted_score >= 0.95)
          )
    ),

    -- H3: street_segment для линий, пересекающихся в 2+ точках (weight 0.9)
    hypothesis_street_segment AS (
        SELECT 
            'street_segment'::VARCHAR(40) AS strategy,
            ST_LineSubstring(
                l.geom,
                LEAST(crossings.first_loc, crossings.last_loc),
                GREATEST(crossings.first_loc, crossings.last_loc)
            ) AS geom,
            l.adjusted_score * 0.9 AS total_score,
            jsonb_build_object(
                'type', 'street_segment',
                'line_id', l.id,
                'crossing_ids', crossings.id_array,
                'segment_length_m', crossings.segment_len_m
            ) AS diagnostics
        FROM (
            SELECT * FROM candidates
            WHERE GeometryType(geom) IN ('LINESTRING', 'MULTILINESTRING')
              AND adjusted_score >= v_score_threshold
        ) l
        CROSS JOIN LATERAL (
            SELECT 
                MIN(ST_LineLocatePoint(l.geom, ST_PointOnSurface(ST_Intersection(l.geom, c.geom)))) AS first_loc,
                MAX(ST_LineLocatePoint(l.geom, ST_PointOnSurface(ST_Intersection(l.geom, c.geom)))) AS last_loc,
                ARRAY_AGG(c.id) AS id_array,
                COUNT(*) AS n,
                ST_Length(ST_ShortestLine(
                    ST_PointN(ST_GeometryN(l.geom, 1), 1),
                    ST_PointN(ST_GeometryN(l.geom, 1), 2)
                )) AS segment_len_m
            FROM candidates c
            WHERE c.id != l.id
              AND ST_Intersects(l.geom, c.geom)
              AND NOT ST_IsEmpty(ST_Intersection(l.geom, c.geom))
        ) crossings
        WHERE crossings.n >= 2
          AND crossings.last_loc > crossings.first_loc
    ),

    -- H4: weighted_centroid для 2+ кандидатов (weight 0.85)
    -- Точки пересечения пар (вес ×2.5) + центроиды кандидатов (вес ×1.0)
    wc_intersection_points AS (
        SELECT 
            ST_PointOnSurface(ST_Intersection(a.geom, b.geom)) AS pt,
            (a.adjusted_score + b.adjusted_score) / 2.0 AS base_score,
            (a.adjusted_score + b.adjusted_score) / 2.0 * 2.5 AS weight
        FROM candidates a
        CROSS JOIN candidates b
        WHERE a.id < b.id
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
    ),
    wc_candidate_points AS (
        SELECT 
            ST_PointOnSurface(geom) AS pt,
            adjusted_score AS base_score,
            adjusted_score * 1.0 AS weight
        FROM candidates
    ),
    wc_all_points AS (
        SELECT pt, weight, base_score FROM wc_intersection_points
        UNION ALL
        SELECT pt, weight, base_score FROM wc_candidate_points
    ),
    hypothesis_weighted_centroid AS (
        SELECT 
            'weighted_centroid'::VARCHAR(40) AS strategy,
            ST_Transform(
                ST_SetSRID(
                    ST_MakePoint(
                        SUM(ST_X(ST_Transform(pt, 3857)) * weight) / NULLIF(SUM(weight), 0),
                        SUM(ST_Y(ST_Transform(pt, 3857)) * weight) / NULLIF(SUM(weight), 0)
                    ),
                    3857
                ),
                4326
            ) AS geom,
            GREATEST(0.1,
                (SELECT AVG(base_score) FROM wc_all_points) * 0.85
                - LEAST(0.3, scatter.distance_m * 0.0004)
            ) AS total_score,
            jsonb_build_object(
                'type', 'weighted_centroid',
                'geo_ids', (SELECT ARRAY_AGG(id) FROM candidates),
                'scatter_m', scatter.distance_m,
                'candidate_count', (SELECT COUNT(*) FROM candidates)
            ) AS diagnostics
        FROM wc_all_points
        CROSS JOIN LATERAL (
            SELECT ST_MaxDistance(
                ST_Collect(ST_Transform(wc_all_points.pt, 3857)),
                ST_Centroid(ST_Collect(ST_Transform(wc_all_points.pt, 3857)))
            ) AS distance_m
            FROM wc_all_points
        ) scatter
        WHERE (SELECT COUNT(*) FROM candidates) >= 2
          AND scatter.distance_m <= v_wc_max_scatter_m
        GROUP BY scatter.distance_m
    ),

    -- Объединение всех гипотез
    all_hypotheses AS (
        SELECT * FROM hypothesis_single
        UNION ALL
        SELECT * FROM hypothesis_intersection
        UNION ALL
        SELECT * FROM hypothesis_street_segment
        UNION ALL
        SELECT * FROM hypothesis_weighted_centroid
    ),

    -- Выбор лучшей гипотезы
    best_hypothesis AS (
        SELECT 
            h.geom,
            h.strategy,
            h.total_score,
            h.diagnostics
        FROM all_hypotheses h
        ORDER BY 
            CASE h.strategy 
                WHEN 'intersection'      THEN 5
                WHEN 'street_segment'    THEN 4
                WHEN 'weighted_centroid' THEN 3
                ELSE 1
            END DESC,
            h.total_score DESC
        LIMIT 1
    )

    -- Возврат лучшей гипотезы или fallback
    SELECT 
        COALESCE(bh.geom, ST_MakeValid(s.geom)),
        COALESCE(bh.strategy, 'single_match'),
        v_matches,
        COALESCE(bh.total_score, v_filtered_scores[1]),
        COALESCE(bh.diagnostics, jsonb_build_object(
            'type', 'single_match',
            'geo_id', v_filtered_ids[1],
            'score', v_filtered_scores[1],
            'reason', 'fallback_best_candidate'
        ))
    INTO v_geom, v_strategy, v_matches, v_confidence, v_diagnostics
    FROM best_hypothesis bh
    LEFT JOIN geo s ON s.id = v_filtered_ids[1];

    -- Fallback: если нет гипотез, берём лучшего кандидата
    IF v_geom IS NULL THEN
        IF v_filtered_scores[1] >= v_score_threshold THEN
            SELECT ST_MakeValid(s.geom) INTO v_geom FROM geo s WHERE s.id = v_filtered_ids[1];
            v_strategy := 'single_match';
            v_confidence := v_filtered_scores[1];
            v_diagnostics := jsonb_build_object(
                'type', 'single_match',
                'geo_id', v_filtered_ids[1],
                'score', v_filtered_scores[1],
                'reason', 'fallback_best_candidate'
            );
        ELSE
            v_geom := ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
            ), 4326);
            v_strategy := 'random';
            v_confidence := 0.0;
            v_diagnostics := jsonb_build_object('reason', 'no_strong_candidates');
            v_matches := '[]'::jsonb;
        END IF;
    END IF;

    RETURN QUERY SELECT v_geom, v_strategy, v_matches, v_confidence, v_diagnostics;
END;
$$;
