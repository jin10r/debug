-- =============================================================================
-- 16-process-candidates-v2.sql
--
-- process_candidates_v2() — Geometry-First PostGIS arbiter.
-- Выбор стратегии исключительно через пространственные отношения.
-- Для случая без геометрии возвращает strategy='random_null' + geom=NULL,
-- чтобы processor сгенерировал случайную точку (R-PR22).
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates_v2(
    p_geo_ids            INTEGER[]   DEFAULT NULL,
    p_scores             DOUBLE PRECISION[] DEFAULT NULL,
    p_texts              TEXT[]      DEFAULT NULL,
    p_hint               VARCHAR     DEFAULT NULL
)
RETURNS TABLE (
    result_strategy      TEXT,
    result_geom          GEOMETRY,
    result_matches       JSONB,
    result_confidence    DOUBLE PRECISION,
    result_diagnostics   JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_scores            DOUBLE PRECISION[];
    v_filtered_ids      INTEGER[];
    v_filtered_scores   DOUBLE PRECISION[];
    v_filtered_texts    TEXT[];
    v_candidate_count   INTEGER;
    v_geo_count         INTEGER;

    v_score_threshold   DOUBLE PRECISION := 0.70;
    v_dwithin_m         DOUBLE PRECISION := 50.0;
    v_intersection_radius_m DOUBLE PRECISION := 200.0;
    v_close_radius_m    DOUBLE PRECISION := 40.0;
    v_wc_max_scatter_m  DOUBLE PRECISION := 1500.0;
    v_ss_min_segment_m  DOUBLE PRECISION := 50.0;
    v_ss_max_segment_m  DOUBLE PRECISION := 2500.0;
    v_anti_list_m       DOUBLE PRECISION := 2000.0;
    v_anti_list_score   DOUBLE PRECISION := 0.85;
BEGIN
    v_scores := COALESCE(
        p_scores,
        ARRAY_FILL(1.0::double precision, ARRAY[COALESCE(array_length(p_geo_ids, 1), 0)])
    );

    -- ── 0 кандидатов на входе → random_null ───────────────────────────────────
    IF p_geo_ids IS NULL OR array_length(p_geo_ids, 1) = 0 THEN
        RETURN QUERY SELECT
            'random_null'::TEXT,
            NULL::GEOMETRY,
            '[]'::JSONB,
            0.0::DOUBLE PRECISION,
            jsonb_build_object('reason', 'no_candidates');
        RETURN;
    END IF;

    v_candidate_count := array_length(p_geo_ids, 1);

    -- ── Нормализация: дедупликация по geo_id (max score), фильтр score >= 0.70,
    --    объединение с geo (geom, geom_m), лимит 10 ────────────────────────────
    WITH raw AS (
        SELECT
            s.id,
            s.type,
            s.names,
            ST_MakeValid(s.geom) AS geom,
            s.geom_m,
            u.score,
            u.text
        FROM geo s
        JOIN unnest(
            p_geo_ids,
            v_scores,
            COALESCE(p_texts, ARRAY_FILL(NULL::text, ARRAY[v_candidate_count]))
        ) AS u(id, score, text) ON s.id = u.id
        WHERE u.score >= v_score_threshold
          AND s.geom IS NOT NULL
    ),
    deduped AS (
        SELECT DISTINCT ON (id)
            id, type, names, geom, geom_m, score, text
        FROM raw
        ORDER BY id, score DESC
    ),
    ordered AS (
        SELECT *
        FROM deduped
        ORDER BY score DESC
        LIMIT 10
    )
    SELECT COALESCE(array_agg(id ORDER BY score DESC), ARRAY[]::INTEGER[]),
           COALESCE(array_agg(score ORDER BY score DESC), ARRAY[]::DOUBLE PRECISION[]),
           COALESCE(array_agg(text ORDER BY score DESC), ARRAY[]::TEXT[])
    INTO v_filtered_ids, v_filtered_scores, v_filtered_texts
    FROM ordered;

    v_geo_count := array_length(v_filtered_ids, 1);

    -- ── 0 валидных кандидатов → random_null ───────────────────────────────────
    IF v_geo_count IS NULL OR v_geo_count = 0 THEN
        RETURN QUERY SELECT
            'random_null'::TEXT,
            NULL::GEOMETRY,
            '[]'::JSONB,
            0.0::DOUBLE PRECISION,
            jsonb_build_object('reason', 'no_valid_candidates');
        RETURN;
    END IF;

    -- ── Формируем matches JSON ────────────────────────────────────────────────
    RETURN QUERY
    WITH candidates AS (
        SELECT
            s.id,
            s.type,
            s.names,
            ST_MakeValid(s.geom) AS geom,
            s.geom_m,
            u.score,
            u.text
        FROM geo s
        JOIN unnest(
            v_filtered_ids,
            v_filtered_scores,
            v_filtered_texts
        ) AS u(id, score, text) ON s.id = u.id
    ),

    -- ── H1: single_match для каждого кандидата (weight 1.0) ──────────────────
    hypothesis_single AS (
        SELECT
            'single_match'::TEXT AS strategy,
            c.geom AS geom,
            c.score AS total_score,
            jsonb_build_object(
                'type', 'single_match',
                'geo_id', c.id,
                'score', c.score
            ) AS diagnostics
        FROM candidates c
    ),

    -- ── H2: street_segment ────────────────────────────────────────────────────
    -- Нужна главная линия, пересекающая/находящаяся в 50м от 2+ кандидатов.
    -- Для MULTILINESTRING берём самый длинный компонент.
    lines AS (
        SELECT
            c.id,
            c.type,
            c.geom AS geom_4326,
            c.geom_m,
            c.score,
            c.names,
            ST_LineMerge(ST_CollectionExtract(c.geom_m, 2)) AS line_m,
            (ST_Length(c.geom_m) / 1000.0)::DOUBLE PRECISION AS length_km
        FROM candidates c
        WHERE ST_GeometryType(c.geom) IN ('ST_LineString', 'ST_MultiLineString')
    ),
    main_line AS (
        SELECT l.*
        FROM lines l
        CROSS JOIN LATERAL (
            SELECT COUNT(*) AS conn
            FROM candidates c
            WHERE c.id != l.id
              AND (
                  ST_Intersects(l.geom_m, c.geom_m)
                  OR ST_DWithin(l.geom_m, c.geom_m, v_dwithin_m)
              )
        ) cx
        WHERE cx.conn >= 2
        ORDER BY l.score DESC, l.length_km DESC
        LIMIT 1
    ),
    normalized_main_line AS (
        SELECT
            ml.id,
            ml.type,
            ml.geom_4326,
            ml.geom_m,
            ml.score,
            ml.names,
            CASE
                WHEN ST_GeometryType(ml.geom_m) = 'ST_MultiLineString'
                THEN ST_GeometryN(
                         ST_CollectionExtract(
                             ST_LineMerge(ml.geom_m), 2
                         ), 1
                     )
                ELSE ml.geom_m
            END AS line_m
        FROM main_line ml
    ),
    street_segment_anchors AS (
        SELECT
            nml.id AS line_id,
            ST_ClosestPoint(
                nml.line_m,
                c.geom_m
            ) AS anchor_m
        FROM normalized_main_line nml
        CROSS JOIN candidates c
        WHERE c.id != nml.id
          AND (
              ST_Intersects(nml.geom_m, c.geom_m)
              OR ST_DWithin(nml.line_m, c.geom_m, v_dwithin_m)
          )
          AND ST_ClosestPoint(nml.line_m, c.geom_m) IS NOT NULL
    ),
    hypothesis_street_segment AS (
        SELECT
            'street_segment'::TEXT AS strategy,
            ST_Transform(
                ST_SetSRID(
                    ST_LineSubstring(
                        nml.line_m,
                        GREATEST(0.001::DOUBLE PRECISION,
                                 LEAST(0.999::DOUBLE PRECISION,
                                       LEAST(ssa.min_frac, ssa.max_frac))),
                        GREATEST(0.001::DOUBLE PRECISION,
                                 LEAST(0.999::DOUBLE PRECISION,
                                       GREATEST(ssa.min_frac, ssa.max_frac)))
                    ),
                    3857
                ),
                4326
            ) AS geom,
            nml.score * 0.9 AS total_score,
            jsonb_build_object(
                'type', 'street_segment',
                'line_id', nml.id,
                'line_names', nml.names,
                'segment_length_m',
                ST_Length(
                    ST_LineSubstring(
                        nml.line_m,
                        GREATEST(0.001::DOUBLE PRECISION,
                                 LEAST(0.999::DOUBLE PRECISION,
                                       LEAST(ssa.min_frac, ssa.max_frac))),
                        GREATEST(0.001::DOUBLE PRECISION,
                                 LEAST(0.999::DOUBLE PRECISION,
                                       GREATEST(ssa.min_frac, ssa.max_frac)))
                    )
                ),
                'crossing_count', ssa.cnt
            ) AS diagnostics
        FROM normalized_main_line nml
        CROSS JOIN LATERAL (
            SELECT
                MIN(ST_LineLocatePoint(nml.line_m, ssa.anchor_m)) AS min_frac,
                MAX(ST_LineLocatePoint(nml.line_m, ssa.anchor_m)) AS max_frac,
                COUNT(*) AS cnt
            FROM street_segment_anchors ssa
            WHERE ssa.line_id = nml.id
        ) ssa
        WHERE ssa.cnt >= 2
          AND ssa.max_frac > ssa.min_frac
          AND ST_Length(
                  ST_LineSubstring(
                      nml.line_m,
                      GREATEST(0.001::DOUBLE PRECISION,
                               LEAST(0.999::DOUBLE PRECISION,
                                     LEAST(ssa.min_frac, ssa.max_frac))),
                      GREATEST(0.001::DOUBLE PRECISION,
                               LEAST(0.999::DOUBLE PRECISION,
                                     GREATEST(ssa.min_frac, ssa.max_frac)))
                  )
              ) BETWEEN v_ss_min_segment_m AND v_ss_max_segment_m
    ),

    -- ── H3: intersection ──────────────────────────────────────────────────────
    -- Только если нет валидного street_segment.
    -- Якоря = РЕАЛЬНЫЕ точки пересечения пар кандидатов (ST_Intersection),
    -- а не середины геометрий: для длинных улиц середины далеки от перекрёстка.
    -- Для не-пересекающихся пар — середина кратчайшей линии, если зазор <= 40м
    -- (компактный кластер POI) или <= 200м и хотя бы один из пары — линия.
    pair_intersections AS (
        SELECT
            ST_PointOnSurface(ST_Intersection(a.geom_m, b.geom_m)) AS pt_m,
            a.id AS id_a,
            b.id AS id_b
        FROM candidates a
        CROSS JOIN candidates b
        WHERE a.id < b.id
          AND ST_Intersects(a.geom_m, b.geom_m)
          AND NOT ST_IsEmpty(ST_Intersection(a.geom_m, b.geom_m))
    ),
    pair_closest AS (
        SELECT
            ST_LineInterpolatePoint(ST_ShortestLine(a.geom_m, b.geom_m), 0.5) AS pt_m,
            a.id AS id_a,
            b.id AS id_b
        FROM candidates a
        CROSS JOIN candidates b
        WHERE a.id < b.id
          AND NOT ST_Intersects(a.geom_m, b.geom_m)
          AND (
              ST_Distance(a.geom_m, b.geom_m) <= v_close_radius_m
              OR (
                  ST_Distance(a.geom_m, b.geom_m) <= v_intersection_radius_m
                  AND (
                      ST_GeometryType(a.geom) IN ('ST_LineString', 'ST_MultiLineString')
                      OR ST_GeometryType(b.geom) IN ('ST_LineString', 'ST_MultiLineString')
                  )
              )
          )
    ),
    intersection_anchors AS (
        SELECT pt_m FROM pair_intersections
        UNION ALL
        SELECT pt_m FROM pair_closest
    ),
    anchor_cluster AS (
        SELECT
            ST_MaxDistance(
                ST_Collect(ia.pt_m),
                ST_Centroid(ST_Collect(ia.pt_m))
            ) AS spread_m,
            COUNT(*) AS cnt
        FROM intersection_anchors ia
    ),
    hypothesis_intersection AS (
        SELECT
            'intersection'::TEXT AS strategy,
            ST_Transform(
                ST_SetSRID(
                    ST_Centroid(ST_Collect(ia.pt_m)),
                    3857
                ),
                4326
            ) AS geom,
            (SELECT AVG(c.score) FROM candidates c)::DOUBLE PRECISION AS total_score,
            jsonb_build_object(
                'type', 'intersection',
                'geo_ids', (SELECT array_agg(id) FROM candidates),
                'spread_m', ac.spread_m,
                'pair_count', ac.cnt
            ) AS diagnostics
        FROM intersection_anchors ia
        CROSS JOIN anchor_cluster ac
        WHERE ac.spread_m <= v_intersection_radius_m
          AND ac.cnt >= 1
          AND (SELECT COUNT(*) FROM candidates) >= 2
        GROUP BY ac.spread_m, ac.cnt
    ),

    -- ── H4: weighted_centroid ─────────────────────────────────────────────────
    -- Только если нет валидного street_segment или intersection.
    wc_intersections AS (
        SELECT
            ST_PointOnSurface(ST_Intersection(a.geom, b.geom)) AS pt_4326,
            ST_Transform(ST_PointOnSurface(ST_Intersection(a.geom, b.geom)), 3857) AS pt_m,
            (a.score + b.score) / 2.0 AS base_score,
            (a.score + b.score) / 2.0 * 2.5 AS weight
        FROM candidates a
        CROSS JOIN candidates b
        WHERE a.id < b.id
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
    ),
    wc_points AS (
        SELECT
            pt_4326,
            pt_m,
            base_score,
            weight
        FROM wc_intersections
        UNION ALL
        SELECT
            ST_PointOnSurface(c.geom) AS pt_4326,
            ST_Transform(ST_PointOnSurface(c.geom), 3857) AS pt_m,
            c.score AS base_score,
            c.score * 1.0 AS weight
        FROM candidates c
    ),
    wc_scatter AS (
        SELECT
            ST_MaxDistance(
                ST_Collect(wp.pt_m),
                ST_Centroid(ST_Collect(wp.pt_m))
            ) AS distance_m
        FROM wc_points wp
    ),
    hypothesis_weighted_centroid AS (
        SELECT
            'weighted_centroid'::TEXT AS strategy,
            ST_Transform(
                ST_SetSRID(
                    ST_MakePoint(
                        SUM(ST_X(wp.pt_m) * wp.weight) / NULLIF(SUM(wp.weight), 0),
                        SUM(ST_Y(wp.pt_m) * wp.weight) / NULLIF(SUM(wp.weight), 0)
                    ),
                    3857
                ),
                4326
            ) AS geom,
            GREATEST(0.1,
                (SELECT AVG(wp.base_score) FROM wc_points wp) * 0.85
                - LEAST(0.3, sc.distance_m * 0.0004)
            ) AS total_score,
            jsonb_build_object(
                'type', 'weighted_centroid',
                'geo_ids', (SELECT array_agg(id) FROM candidates),
                'scatter_m', sc.distance_m,
                'candidate_count', (SELECT COUNT(*) FROM candidates)
            ) AS diagnostics
        FROM wc_points wp
        CROSS JOIN wc_scatter sc
        WHERE (SELECT COUNT(*) FROM candidates) >= 2
          AND sc.distance_m <= v_wc_max_scatter_m
        GROUP BY sc.distance_m
    ),

    -- ── Объединение гипотез ───────────────────────────────────────────────────
    all_hypotheses AS (
        SELECT * FROM hypothesis_street_segment
        UNION ALL
        SELECT * FROM hypothesis_intersection
        UNION ALL
        SELECT * FROM hypothesis_weighted_centroid
        UNION ALL
        SELECT * FROM hypothesis_single
    ),

    -- ── Выбор лучшей гипотезы ─────────────────────────────────────────────────
    best_hypothesis AS (
        SELECT
            h.strategy,
            h.geom,
            h.total_score,
            h.diagnostics
        FROM all_hypotheses h
        ORDER BY
            CASE h.strategy
                WHEN 'street_segment'    THEN 5
                WHEN 'intersection'      THEN 4
                WHEN 'weighted_centroid' THEN 3
                ELSE 1
            END DESC,
            h.total_score DESC
        LIMIT 1
    ),

    -- ── Single-match fallback: лучший кандидат по score ──────────────────────
    best_single AS (
        SELECT
            c.geom,
            c.score AS total_score,
            jsonb_build_object(
                'type', 'single_match',
                'geo_id', c.id,
                'score', c.score
            ) AS diagnostics
        FROM candidates c
        ORDER BY
            CASE
                WHEN ST_GeometryType(c.geom) IN (
                    'ST_LineString', 'ST_MultiLineString'
                ) THEN 2
                ELSE 1
            END DESC,
            ST_Length(ST_Transform(c.geom, 3857)) DESC,
            c.id ASC
        LIMIT 1
    ),

    -- ── Anti-list Guard: сильный кандидат-выброс (>2000м от ГЕОМЕТРИИ
    --    кандидата, а не от его середины) → полный fallback на best_single ────
    anti_list_trigger AS (
        SELECT
            bh.strategy,
            bh.geom,
            bh.total_score,
            bh.diagnostics,
            bh.strategy IS NOT NULL
            AND EXISTS (
                SELECT 1
                FROM candidates c
                WHERE c.score >= v_anti_list_score
                  AND ST_Distance(
                          ST_Transform(bh.geom, 3857),
                          c.geom_m
                      ) > v_anti_list_m
            ) AS triggered
        FROM best_hypothesis bh
    ),
    anti_list_check AS (
        SELECT
            CASE WHEN alt.triggered THEN 'single_match' ELSE alt.strategy END AS safe_strategy,
            CASE WHEN alt.triggered THEN bs.geom ELSE alt.geom END AS safe_geom,
            CASE WHEN alt.triggered THEN bs.total_score ELSE alt.total_score END AS safe_confidence,
            CASE WHEN alt.triggered THEN bs.diagnostics ELSE alt.diagnostics END AS safe_diagnostics
        FROM anti_list_trigger alt
        CROSS JOIN best_single bs
    ),

    final_result AS (
        SELECT
            COALESCE(alc.safe_strategy, 'single_match') AS strategy,
            COALESCE(alc.safe_geom, bs.geom) AS geom,
            COALESCE(alc.safe_confidence, bs.total_score) AS confidence,
            COALESCE(alc.safe_diagnostics, bs.diagnostics) AS diagnostics
        FROM anti_list_check alc
        LEFT JOIN best_single bs ON 1=1
    )

    SELECT
        fr.strategy,
        fr.geom,
        (SELECT jsonb_agg(
            jsonb_build_object(
                'geo_id', c.id,
                'name', c.names[1],
                'similarity', c.score,
                'matched_text', c.text
            ) ORDER BY c.score DESC
        ) FROM candidates c) AS matches,
        fr.confidence,
        fr.diagnostics
    FROM final_result fr;
END;
$$;
