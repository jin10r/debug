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
    p_strategy          VARCHAR(40) DEFAULT NULL,
    p_center_lon        FLOAT   DEFAULT 30.83135,
    p_center_lat        FLOAT   DEFAULT 46.49804,
    p_radius            FLOAT   DEFAULT 0.045
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
    v_filtered_ids     INT[];
    v_filtered_scores  FLOAT[];
    v_true_count       INT;
    v_true_collected   GEOMETRY;
    v_pseudo_count     INT;
    v_pseudo_collected GEOMETRY;
    v_score_threshold  FLOAT := 0.85;   -- geom_min_score для strong_geoms
    v_pseudo_radius    FLOAT := 150.0;  -- м, радиус ПСЕВДОПЕРЕСЕЧЕНИЯ (intersection-ветка)
    v_midpoint_max_radius FLOAT := 3000.0; -- м, верхний предел midpoint (CAP): дальше — ложные пары
    -- синхронизировать с _MIDPOINT_TYPES в processor/semantic_resolver.py
    v_midpoint_types   TEXT[] := ARRAY['street', 'market', 'station', 'park',
                                       'landmark', 'village', 'town'];
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
            '[]'::jsonb;
        RETURN;
    END IF;

    -- ── Фильтрация кандидатов по району ───────────────────────────────────────
    -- Если среди raw_candidates есть district, он служит пространственным
    -- фильтром: остальные кандидаты должны быть ST_Within этого района.
    -- Сам район исключается из final_candidates и не участвует в стратегии.
    WITH
    raw_candidates AS (
        SELECT s.id, s.type, s.geom, u.score
        FROM geo s
        JOIN unnest(p_geo_ids, v_scores) AS u(id, score) ON s.id = u.id
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
           COALESCE(array_agg(score ORDER BY score DESC), ARRAY[]::FLOAT[])
    INTO v_filtered_ids, v_filtered_scores
    FROM final_candidates;

    -- ── 0 кандидатов после фильтрации: случайная точка ────────────────────────
    IF COALESCE(array_length(v_filtered_ids, 1), 0) = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
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
    ) AS u(id, score, part) ON s.id = u.id
    WHERE s.id = ANY(v_filtered_ids);

    -- ── 1 совпадение или стратегия single_match ───────────────────────────────
    -- Детерминированная защита вместо SemanticMatcher (docs §13.4):
    -- single_match-геометрия допустима ТОЛЬКО для уверенных кандидатов
    -- (score >= v_score_threshold = 0.85, geometry_min_score). Слабые typo-
    -- кандидаты (0.80–0.85) без семантического подтверждения НЕ привязывают
    -- событие к объекту — иначе ложный матч («переезд»→«МЕТРО» 0.8) даёт
    -- неверную геометрию (событие 237 «Станция студенческая»).
    IF array_length(v_filtered_ids, 1) = 1 OR p_strategy = 'single_match' THEN
        IF v_filtered_scores[1] >= v_score_threshold THEN
            SELECT ST_MakeValid(geom) INTO v_geom FROM geo WHERE id = v_filtered_ids[1];
            RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        ELSE
            RETURN QUERY SELECT
                ST_SetSRID(ST_MakePoint(
                    p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                    p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
                ), 4326),
                'random'::VARCHAR(40),
                '[]'::jsonb;
        END IF;
        RETURN;
    END IF;

    -- ── Стратегия midpoint (вычисление, только для разрешённых типов) ──────────
    -- Семантика (по решению пользователя):
    --   • пары БЛИЖЕ 150 м (v_pseudo_radius) НЕ дают midpoint — кограничные;
    --     для них существует пересечение/псевдопересечение в intersection-ветке;
    --   • midpoint — только для пар БЕЗ пересечения в диапазоне
    --     (v_pseudo_radius, v_midpoint_max_radius] — «между X и Y» далёких объектов;
    --   • при нескольких парах берётся САМАЯ БЛИЗКАЯ (точнее к месту события);
    --   • нет пар в диапазоне → fallback single_match.
    -- Явная midpoint-стратегия. Та же детерминированная защита (docs §13.4):
    -- слабые кандидаты (< 0.85) НЕ участвуют — иначе два typo-матча 0.80–0.85
    -- дали бы ложный midpoint (как ложный single_match у события 237).
    -- Параллель с auto-веткой: там порог применяет strong_geoms.
    IF p_strategy = 'midpoint' THEN
        WITH
        valid_geoms AS (
            SELECT s.id, ST_MakeValid(s.geom) AS geom,
                   ST_Transform(ST_MakeValid(s.geom), 3857) AS geom_m
            FROM geo s
            JOIN unnest(v_filtered_ids, v_filtered_scores)
                 AS u(id, score) ON s.id = u.id
            WHERE s.id = ANY(v_filtered_ids)
              AND u.score >= v_score_threshold
              AND s.geom IS NOT NULL
              AND s.type = ANY(v_midpoint_types)
        ),
        pairs AS (
            SELECT a.id AS id1, b.id AS id2,
                   a.geom AS geom1, b.geom AS geom2,
                   ST_Distance(a.geom_m, b.geom_m) AS dist_m
            FROM valid_geoms a
            CROSS JOIN valid_geoms b
            WHERE a.id < b.id
              AND NOT ST_Intersects(a.geom, b.geom)
              AND ST_Distance(a.geom_m, b.geom_m) > v_pseudo_radius
              AND ST_Distance(a.geom_m, b.geom_m) <= v_midpoint_max_radius
        ),
        best_pair AS (
            SELECT geom1, geom2
            FROM pairs
            ORDER BY dist_m ASC
            LIMIT 1
        )
        SELECT ST_LineInterpolatePoint(ST_ShortestLine(geom1, geom2), 0.5)
        INTO v_geom
        FROM best_pair;

        IF v_geom IS NOT NULL THEN
            RETURN QUERY SELECT v_geom, 'midpoint'::VARCHAR(40), v_matches;
            RETURN;
        END IF;

        -- Нет пар в (150 м, 3 км]: не форсируем single_match — продолжаем в
        -- auto-секцию ниже. Она вернёт true-пересечение (например «по X от Y»
        -- на перекрёстке), затем псевдопересечение ≤150 м, затем single_match.
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
            WHERE s.id = ANY(v_filtered_ids)
              AND s.geom IS NOT NULL
        ) sub
        ORDER BY geom_hash, id
    ),
    strong_geoms AS (
        SELECT ug.id, ug.geom, ug.geom_m
        FROM unique_geoms ug
        WHERE EXISTS (
            SELECT 1 FROM unnest(v_filtered_ids, v_filtered_scores) AS u(id, score)
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
            -- Несколько точек пересечения (3+ кандидата): ST_ConvexHull сам по
            -- себе дал бы LINESTRING/POLYGON (событие растягивалось до 1.3 км).
            -- PointOnSurface гарантированно возвращает POINT внутри кластера.
            v_geom     := ST_PointOnSurface(ST_ConvexHull(v_true_collected));
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

    -- Приоритет 3: нет пространственной связи → лучший объект по score.
    -- Та же детерминированная защита (docs §13.4): слабый лучший кандидат
    -- (< 0.85) НЕ даёт single_match-геометрию → random.
    ELSE
        IF v_filtered_scores[1] >= v_score_threshold THEN
            SELECT ST_MakeValid(s.geom) INTO v_geom
            FROM geo s
            JOIN unnest(v_filtered_ids, v_filtered_scores) AS u(id, score) ON s.id = u.id
            ORDER BY u.score DESC
            LIMIT 1;
            v_strategy := 'single_match';
        ELSE
            v_geom := ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
            ), 4326);
            v_strategy := 'random';
            v_matches := '[]'::jsonb;
        END IF;
    END IF;

    -- Защитный fallback (та же проверка порога, docs §13.4)
    IF v_geom IS NULL THEN
        IF v_filtered_scores[1] >= v_score_threshold THEN
            SELECT ST_MakeValid(s.geom) INTO v_geom
            FROM geo s
            JOIN unnest(v_filtered_ids, v_filtered_scores) AS u(id, score) ON s.id = u.id
            ORDER BY u.score DESC
            LIMIT 1;
            v_strategy := 'single_match';
        ELSE
            v_geom := ST_SetSRID(ST_MakePoint(
                p_center_lon + p_radius * sqrt(random()) * cos(2 * pi() * random()),
                p_center_lat + p_radius * sqrt(random()) * sin(2 * pi() * random())
            ), 4326);
            v_strategy := 'random';
            v_matches := '[]'::jsonb;
        END IF;
    END IF;

    RETURN QUERY SELECT v_geom, v_strategy, v_matches;
END;
$$;
