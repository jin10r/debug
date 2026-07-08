-- =============================================================================
-- process_candidates.sql
--
-- Вычисляет геометрию события по списку кандидатов от GeoMatcher.
-- Стратегия определяется пространственным анализом, а не текстовыми паттернами.
--
-- Этапы:
--   1. Дедубликация кандидатов (по геометрии + по имени)
--   2. Приоритетная цепочка пространственных проверок в одном WITH:
--      intersection → area (polygon) → pseudo_intersection → proximity → centroid → single_match
--   3. Формирование matches JSON из отфильтрованных кандидатов
--
-- Параметры:
--   p_geo_ids       — ID кандидатов из таблицы geo (INT[])
--   p_scores        — scores соответствия (FLOAT[], 0-1)
--   p_matched_texts — текст, который сматчился (TEXT[])
--   p_strategy      — зарезервировано, игнорируется (всегда NULL)
--   p_geo_types     — типы геометрий (TEXT[], зарезервировано)
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_geo_ids           INT[]   DEFAULT NULL,
    p_scores            FLOAT[] DEFAULT NULL,
    p_matched_texts     TEXT[]  DEFAULT NULL,
    p_strategy          VARCHAR(40) DEFAULT NULL,
    p_geo_types         TEXT[]  DEFAULT NULL
)
RETURNS TABLE(
    result_geom     GEOMETRY,
    result_strategy VARCHAR(40),
    result_matches  JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_geom               GEOMETRY;
    v_strategy           VARCHAR(40);
    v_matches            JSONB;
    v_scores             FLOAT[];
    v_n_candidates       INT;
    v_intersection_cnt   INT;
    v_pseudo_cnt         INT;
    v_proximity_cnt      INT;
    v_candidate_cnt      INT;
    v_cluster_centroid   GEOMETRY;
    v_all_within_1km     BOOLEAN;
    v_isect_collected    GEOMETRY;
    v_pseudo_collected   GEOMETRY;
    v_prox_collected     GEOMETRY;
    v_cand_geoms         GEOMETRY;
    v_score_min          FLOAT := 0.80;
    v_pseudo_radius      FLOAT := 150.0;
    v_proximity_radius   FLOAT := 500.0;
    v_cluster_radius     FLOAT := 1000.0;
BEGIN
    v_scores := COALESCE(
        p_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_geo_ids, 1), 0)])
    );
    v_n_candidates := COALESCE(array_length(p_geo_ids, 1), 0);

    -- ── 0 кандидатов: случайная точка ─────────────────────────────────────────
    IF v_n_candidates = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                30.7233 + 0.09 * (random() - 0.5),
                46.4825 + 0.09 * (random() - 0.5)
            ), 4326),
            'random'::VARCHAR(40),
            '[]'::jsonb;
        RETURN;
    END IF;

    -- ── 1 кандидат: single_match ──────────────────────────────────────────────
    IF v_n_candidates = 1 THEN
        SELECT ST_MakeValid(geom) INTO v_geom FROM geo WHERE id = p_geo_ids[1];

        SELECT jsonb_build_array(jsonb_build_object(
            'geo_id',       s.id,
            'name',         s.names[1],
            'similarity',   v_scores[1],
            'matched_text', COALESCE(p_matched_texts[1], '')
        )) INTO v_matches
        FROM geo s WHERE s.id = p_geo_ids[1];

        RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        RETURN;
    END IF;

    -- ── 2+ кандидатов: пространственный анализ ────────────────────────────────
    -- Все вычисления в одном WITH: дедубликация + пересечения + matches
    WITH

    -- Исходные кандидаты с геометриями и score
    raw_candidates AS (
        SELECT s.id, s.names[1] AS name,
               ST_MakeValid(s.geom) AS geom,
               ST_Transform(ST_MakeValid(s.geom), 3857) AS geom_m,
               u.score
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
        WHERE s.geom IS NOT NULL
    ),

    -- Шаг A: дедубликация по идентичной геометрии (SnapToGrid 0.0001° ≈ 10 м)
    geom_dedup AS (
        SELECT DISTINCT ON (ST_AsText(ST_SnapToGrid(geom, 0.0001)))
            id, name, geom, geom_m, score
        FROM raw_candidates
        ORDER BY ST_AsText(ST_SnapToGrid(geom, 0.0001)), score DESC
    ),

    -- Шаг B: дедубликация по имени
    -- Для одноимённых кандидатов: оставить ближайший к центроиду их геометрий
    name_centroids AS (
        SELECT name, ST_Centroid(ST_Collect(geom_m)) AS centroid
        FROM geom_dedup
        GROUP BY name
    ),
    name_ranked AS (
        SELECT cd.*, ROW_NUMBER() OVER (
            PARTITION BY cd.name
            ORDER BY ST_Distance(cd.geom_m, nc.centroid)
        ) AS rn
        FROM geom_dedup cd
        JOIN name_centroids nc ON cd.name = nc.name
    ),

    -- Финальный набор кандидатов после полной дедубликации
    candidates AS (
        SELECT id, name, geom, geom_m, score
        FROM name_ranked
        WHERE rn = 1
    ),

    -- (1) Истинные пересечения: ST_Intersects + score >= порога
    intersection_points AS (
        SELECT DISTINCT ST_PointOnSurface(ST_Intersection(a.geom, b.geom)) AS point
        FROM candidates a
        CROSS JOIN candidates b
        WHERE a.id < b.id
          AND a.score >= v_score_min AND b.score >= v_score_min
          AND ST_Intersects(a.geom, b.geom)
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
    ),

    -- (2) Кластеризация: центроид точек пересечения для проверки area
    intersection_cluster AS (
        SELECT
            COUNT(*) AS pt_count,
            ST_Centroid(ST_Collect(point)) AS centroid,
            ST_Collect(point) AS points
        FROM intersection_points
    ),

    -- (3) Псевдопересечения: ST_DWithin(150 м), нет истинного пересечения
    pseudo_points AS (
        SELECT ST_LineInterpolatePoint(
            ST_ShortestLine(a.geom, b.geom), 0.5
        ) AS point
        FROM candidates a, candidates b
        WHERE a.id < b.id
          AND a.score >= v_score_min AND b.score >= v_score_min
          AND NOT ST_Intersects(a.geom, b.geom)
          AND ST_DWithin(a.geom_m, b.geom_m, v_pseudo_radius)
    ),

    -- (4) Проксимити: ST_DWithin(500 м), не вошли в intersection/pseudo
    proximity_points AS (
        SELECT ST_LineInterpolatePoint(
            ST_ShortestLine(a.geom, b.geom), 0.5
        ) AS point
        FROM candidates a, candidates b
        WHERE a.id < b.id
          AND a.score >= v_score_min AND b.score >= v_score_min
          AND NOT ST_Intersects(a.geom, b.geom)
          AND NOT ST_DWithin(a.geom_m, b.geom_m, v_pseudo_radius)
          AND ST_DWithin(a.geom_m, b.geom_m, v_proximity_radius)
    ),

    -- (5) Matches JSON из дедублицированных кандидатов
    matches_built AS (
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'geo_id',       s.id,
                'name',         s.names[1],
                'similarity',   u.score,
                'matched_text', u.part
            ) ORDER BY u.score DESC
        ), '[]'::jsonb) AS matches
        FROM geo s
        JOIN (
            SELECT unnest(p_geo_ids) AS id,
                   unnest(v_scores) AS score,
                   unnest(COALESCE(p_matched_texts,
                       ARRAY_FILL(NULL::text, ARRAY[v_n_candidates])
                   )) AS part
        ) AS u(id, score, part) ON s.id = u.id
        WHERE s.id IN (SELECT id FROM candidates)
    )

    -- Собираем всё в одной выборке
    SELECT
        COALESCE((SELECT pt_count FROM intersection_cluster), 0),
        COALESCE((SELECT COUNT(*) FROM pseudo_points), 0),
        COALESCE((SELECT COUNT(*) FROM proximity_points), 0),
        (SELECT COUNT(*) FROM candidates),
        (SELECT centroid FROM intersection_cluster WHERE pt_count > 0),
        (SELECT ST_Collect(point) FROM intersection_points),
        (SELECT ST_Collect(point) FROM pseudo_points),
        (SELECT ST_Collect(point) FROM proximity_points),
        (SELECT ST_Collect(geom) FROM candidates),
        (SELECT matches FROM matches_built)
    INTO
        v_intersection_cnt, v_pseudo_cnt, v_proximity_cnt,
        v_candidate_cnt,
        v_cluster_centroid,
        v_isect_collected, v_pseudo_collected, v_prox_collected,
        v_cand_geoms, v_matches;

    -- ── Приоритет 1: Истинное пересечение (одна точка) ────────────────────────
    IF v_intersection_cnt = 1 THEN
        v_geom := ST_GeometryN(v_isect_collected, 1);
        v_strategy := 'intersection';

    -- ── Приоритет 2: Кластер пересечений → polygon (все точки в 1 км) ────────
    ELSIF v_intersection_cnt >= 2 THEN
        SELECT bool_and(
            ST_Distance(
                ST_Transform(ST_GeometryN(v_isect_collected, gs.n), 3857),
                ST_Transform(v_cluster_centroid, 3857)
            ) <= v_cluster_radius
        ) INTO v_all_within_1km
        FROM generate_series(1, v_intersection_cnt) AS gs(n);

        IF v_all_within_1km THEN
            v_geom := ST_ConvexHull(v_isect_collected);
            v_strategy := 'area';
        ELSE
            v_geom := v_cluster_centroid;
            v_strategy := 'intersection';
        END IF;

    -- ── Приоритет 3: Псевдопересечение (150 м) ───────────────────────────────
    ELSIF v_pseudo_cnt > 0 THEN
        IF v_pseudo_cnt = 1 THEN
            v_geom := ST_GeometryN(v_pseudo_collected, 1);
        ELSE
            v_geom := ST_Centroid(v_pseudo_collected);
        END IF;
        v_strategy := 'pseudo_intersection';

    -- ── Приоритет 4: Проксимити (500 м) ──────────────────────────────────────
    ELSIF v_proximity_cnt > 0 THEN
        IF v_proximity_cnt = 1 THEN
            v_geom := ST_GeometryN(v_prox_collected, 1);
        ELSE
            v_geom := ST_Centroid(v_prox_collected);
        END IF;
        v_strategy := 'proximity';

    -- ── Приоритет 5: Центроид всех кандидатов ────────────────────────────────
    ELSIF v_candidate_cnt >= 2 THEN
        v_geom := ST_Centroid(v_cand_geoms);
        v_strategy := 'centroid';

    -- ── Приоритет 6: Лучший по score ─────────────────────────────────────────
    ELSE
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';
    END IF;

    -- ── Защитный fallback ─────────────────────────────────────────────────────
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
