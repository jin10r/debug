-- =============================================================================
-- process_candidates.sql
--
-- Логика (приоритеты строго по порядку):
--   0 совпадений  → random точка в зоне событий без локации
--   1 совпадение  → полная геометрия объекта
--   2+ совпадений:
--     1. Все пересечения геометрий одной операцией → 1 pt=Point, 2=LineString, 3+=Polygon
--     2. Нет пересечений → псевдопересечения ≤ 150 м → та же логика
--     3. Нет ни пересечений, ни псевдопересечений → полная геометрия объекта с лучшим score
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_street_ids           INT[]   DEFAULT NULL,
    p_street_scores        FLOAT[] DEFAULT NULL,
    p_pseudo_radius_meters FLOAT   DEFAULT 150.0,
    p_matched_parts        TEXT[]  DEFAULT NULL
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
BEGIN
    v_scores := COALESCE(
        p_street_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_street_ids, 1), 0)])
    );

    -- ── 0 совпадений: случайная точка в зоне событий без локации ──────────────
    IF p_street_ids IS NULL OR array_length(p_street_ids, 1) = 0 THEN
        RETURN QUERY SELECT
            ST_SetSRID(ST_MakePoint(
                30.7233 + 0.09 * (random() - 0.5),
                46.4825 + 0.09 * (random() - 0.5)
            ), 4326),
            'random'::VARCHAR(40),
            '[]'::jsonb;
        RETURN;
    END IF;

    -- ── Формируем matches JSON (переиспользуется во всех ветках) ──────────────
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'street_id',    s.id,
            'name',         s.names[1],
            'similarity',   u.score,
            'matched_part', u.part
        ) ORDER BY u.score DESC
    ), '[]'::jsonb)
    INTO v_matches
    FROM streets s
    JOIN unnest(
        p_street_ids,
        v_scores,
        COALESCE(p_matched_parts, ARRAY_FILL(NULL::text, ARRAY[array_length(p_street_ids, 1)]))
    ) AS u(id, score, part) ON s.id = u.id;

    -- ── 1 совпадение: полная геометрия объекта ────────────────────────────────
    IF array_length(p_street_ids, 1) = 1 THEN
        SELECT ST_MakeValid(geom) INTO v_geom FROM streets WHERE id = p_street_ids[1];
        RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        RETURN;
    END IF;

    -- ── 2+ совпадений: ищем пространственные связи ────────────────────────────
    --
    -- unique_geoms: дедупликация синонимов + предвычисление MakeValid и SRID 3857.
    -- intersections + pseudo_intersections находятся одной операцией (CROSS JOIN
    -- всех уникальных геометрий), результаты агрегируются раздельно через FILTER.
    --
    WITH
    unique_geoms AS (
        SELECT DISTINCT ON (geom_hash) id, geom, geom_m
        FROM (
            SELECT
                s.id,
                ST_MakeValid(s.geom)                                   AS geom,
                ST_Transform(ST_MakeValid(s.geom), 3857)               AS geom_m,
                ST_AsText(ST_SnapToGrid(ST_MakeValid(s.geom), 0.0001)) AS geom_hash
            FROM streets s
            WHERE s.id = ANY(p_street_ids)
              AND s.geom IS NOT NULL
        ) sub
        ORDER BY geom_hash, id
    ),
    -- Истинные пересечения: ST_Intersection вычисляется один раз через LATERAL.
    -- ST_PointOnSurface возвращает точку для любого типа результата (POINT/LINE/POLY).
    intersections AS (
        SELECT ST_PointOnSurface(isect.g) AS point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        CROSS JOIN LATERAL (SELECT ST_Intersection(a.geom, b.geom) AS g) isect
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(isect.g)
    ),
    -- Псевдопересечения: только пары, которые НЕ пересекаются физически
    -- (иначе они попали бы в intersections и считались бы дважды).
    pseudo_intersections AS (
        SELECT ST_Centroid(ST_Collect(
                   ST_ClosestPoint(a.geom, b.geom),
                   ST_ClosestPoint(b.geom, a.geom)
               )) AS point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND NOT ST_Intersects(a.geom, b.geom)
          AND ST_DWithin(a.geom_m, b.geom_m, p_pseudo_radius_meters)
    )
    -- Один агрегирующий проход, результаты разделены через FILTER
    SELECT
        COUNT(*)        FILTER (WHERE src = 'true'   AND point IS NOT NULL)::INT,
        ST_Collect(point) FILTER (WHERE src = 'true' AND point IS NOT NULL),
        COUNT(*)        FILTER (WHERE src = 'pseudo'  AND point IS NOT NULL)::INT,
        ST_Collect(point) FILTER (WHERE src = 'pseudo' AND point IS NOT NULL)
    INTO v_true_count, v_true_collected, v_pseudo_count, v_pseudo_collected
    FROM (
        SELECT point, 'true'   AS src FROM intersections
        UNION ALL
        SELECT point, 'pseudo' AS src FROM pseudo_intersections
    ) combined;

    -- ── Приоритет 1: истинные пересечения ────────────────────────────────────
    IF v_true_count > 0 THEN
        IF v_true_count = 1 THEN
            v_geom     := ST_GeometryN(v_true_collected, 1);
            v_strategy := 'single_intersection';
        ELSE
            -- 2 точки → LineString, 3+ точки → Polygon (ST_ConvexHull выбирает сам)
            v_geom     := ST_ConvexHull(v_true_collected);
            v_strategy := 'polygon_intersection';
        END IF;

    -- ── Приоритет 2: псевдопересечения ───────────────────────────────────────
    ELSIF v_pseudo_count > 0 THEN
        IF v_pseudo_count = 1 THEN
            v_geom     := ST_GeometryN(v_pseudo_collected, 1);
            v_strategy := 'single_intersection';
        ELSE
            v_geom     := ST_ConvexHull(v_pseudo_collected);
            v_strategy := 'polygon_intersection';
        END IF;

    -- ── Приоритет 3: нет пространственной связи → лучший объект по score ─────
    ELSE
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM streets s
        JOIN unnest(p_street_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';
    END IF;

    -- ── Защитный fallback (NULL не должен дойти до INSERT) ───────────────────
    IF v_geom IS NULL THEN
        RAISE WARNING 'process_candidates: первый проход вернул NULL для ids %, аварийный fallback', p_street_ids;
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM streets s
        JOIN unnest(p_street_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';
    END IF;

    RETURN QUERY SELECT v_geom, v_strategy, v_matches;
END;
$$;

-- =============================================================================
-- Тестирование
-- =============================================================================

-- Тест 0: Нет кандидатов → random
-- SELECT result_strategy, ST_GeometryType(result_geom) FROM process_candidates(NULL, NULL);

-- Тест 1: Одна улица → single_match + полная геометрия
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45], ARRAY[0.8]);

-- Тест 2: Две улицы с пересечением → single_intersection + Point
-- SELECT result_strategy, ST_GeometryType(result_geom) FROM process_candidates(ARRAY[45, 123], ARRAY[0.9, 0.7]);

-- Тест 3: Две улицы без пересечения, в радиусе 150м → single_intersection + Point
-- SELECT result_strategy, ST_GeometryType(result_geom) FROM process_candidates(ARRAY[45, 200], ARRAY[0.9, 0.7], 150.0);

-- Тест 4: Две улицы без пересечения, далеко → single_match + полная геометрия лучшей
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45, 200], ARRAY[0.9, 0.7], 10.0);

-- Тест 5: Три улицы, два пересечения → polygon_intersection + LineString/Polygon
-- SELECT result_strategy, ST_GeometryType(result_geom) FROM process_candidates(ARRAY[45, 46, 123], ARRAY[0.9, 0.85, 0.8]);
