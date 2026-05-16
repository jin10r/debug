-- =============================================================================
-- process_candidates.sql
-- Логика обработки кандидатов в PostGIS:
-- 1. Дедупликация по геометрии
-- 2. Истинные пересечения (любой тип → ST_PointOnSurface)
-- 3. Псевдопересечения только для НЕ пересекающихся пар (нет двойного счёта)
-- 4. Итоговая геометрия: 0 точек → лучший объект, 1 → Point, 2+ → ConvexHull
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_street_ids           INT[]   DEFAULT NULL,
    p_street_scores        FLOAT[] DEFAULT NULL,
    p_pseudo_radius_meters FLOAT   DEFAULT 100.0
)
RETURNS TABLE(
    result_geom     GEOMETRY,
    result_strategy VARCHAR(40),
    result_matches  JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_geom      GEOMETRY;
    v_strategy  VARCHAR(40);
    v_matches   JSONB;
    v_count     INT;
    v_collected GEOMETRY;
    v_scores    FLOAT[];
BEGIN
    -- Нормализация массива scores (один раз, используется в нескольких местах)
    v_scores := COALESCE(
        p_street_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_street_ids, 1), 0)])
    );

    -- ── Нет кандидатов → случайная точка в Одессе ─────────────────────────────
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

    -- ── Формируем matches JSON ─────────────────────────────────────────────────
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'street_id',    s.id,
            'name',         s.names[1],
            'similarity',   u.score,
            'matched_part', 'full_text'
        ) ORDER BY u.score DESC
    ), '[]'::jsonb)
    INTO v_matches
    FROM streets s
    JOIN unnest(p_street_ids, v_scores) AS u(id, score) ON s.id = u.id;

    -- ── Одна улица → возвращаем её геометрию ──────────────────────────────────
    IF array_length(p_street_ids, 1) = 1 THEN
        SELECT ST_MakeValid(geom) INTO v_geom FROM streets WHERE id = p_street_ids[1];
        RETURN QUERY SELECT v_geom, 'single_match'::VARCHAR(40), v_matches;
        RETURN;
    END IF;

    -- ── 2+ улицы: ищем пространственные связи ─────────────────────────────────
    WITH
    street_geoms AS (
        -- Предвычисляем MakeValid и проекцию в метрах (один раз на улицу)
        SELECT
            s.id,
            ST_MakeValid(s.geom)                                   AS geom,
            ST_Transform(ST_MakeValid(s.geom), 3857)               AS geom_m,
            ST_AsText(ST_SnapToGrid(ST_MakeValid(s.geom), 0.0001)) AS geom_hash
        FROM streets s
        WHERE s.id = ANY(p_street_ids)
          AND s.geom IS NOT NULL
    ),
    unique_geoms AS (
        -- Дедупликация синонимов с одинаковой геометрией
        SELECT DISTINCT ON (geom_hash) id, geom, geom_m
        FROM street_geoms
        ORDER BY geom_hash, id
    ),
    intersections AS (
        -- Истинные пересечения: ST_Intersection вычисляется один раз через LATERAL.
        -- ST_PointOnSurface корректно обрабатывает POINT/LINE/POLYGON результат.
        SELECT ST_PointOnSurface(isect.g) AS point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        CROSS JOIN LATERAL (SELECT ST_Intersection(a.geom, b.geom) AS g) isect
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(isect.g)
    ),
    pseudo_intersections AS (
        -- Псевдопересечения только для пар, которые НЕ пересекаются физически.
        -- Исключение пересекающихся пар устраняет двойной счёт в all_points.
        SELECT ST_Centroid(ST_Collect(
                   ST_ClosestPoint(a.geom, b.geom),
                   ST_ClosestPoint(b.geom, a.geom)
               )) AS point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        WHERE a.id < b.id
          AND ST_IsValid(a.geom) AND ST_IsValid(b.geom)
          AND NOT ST_Intersects(a.geom, b.geom)          -- исключаем уже пересекающиеся
          AND ST_DWithin(a.geom_m, b.geom_m, p_pseudo_radius_meters)
    )
    -- Агрегируем все точки за один проход: COUNT + ST_Collect
    SELECT COUNT(pt.point)::INT, ST_Collect(pt.point)
    INTO   v_count, v_collected
    FROM  (
        SELECT point FROM intersections
        UNION ALL
        SELECT point FROM pseudo_intersections
    ) pt
    WHERE pt.point IS NOT NULL;

    -- ── Выбираем итоговую геометрию по количеству найденных точек ─────────────
    IF v_count = 0 THEN
        -- Нет пространственных связей → полная геометрия улицы с лучшим score
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM streets s
        JOIN unnest(p_street_ids, v_scores) AS u(id, score) ON s.id = u.id
        ORDER BY u.score DESC
        LIMIT 1;
        v_strategy := 'single_match';

    ELSIF v_count = 1 THEN
        -- Ровно одно пересечение → точка
        v_geom     := ST_GeometryN(v_collected, 1);
        v_strategy := 'single_intersection';

    ELSE
        -- Несколько пересечений → выпуклая оболочка
        v_geom     := ST_ConvexHull(v_collected);
        v_strategy := 'polygon_intersection';
    END IF;

    -- ── Fallback: геометрия осталась NULL ─────────────────────────────────────
    IF v_geom IS NULL THEN
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

-- Тест 1: Нет кандидатов (random)
-- SELECT * FROM process_candidates(NULL, NULL);

-- Тест 2: Одна улица (single_match)
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45], ARRAY[0.8]);

-- Тест 3: Две улицы с пересечением (single_intersection)
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45, 123], ARRAY[0.9, 0.7]);

-- Тест 4: Две улицы без пересечения, далеко (single_match)
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45, 200], ARRAY[0.9, 0.7]);

-- Тест 5: Много улиц с несколькими пересечениями (polygon_intersection)
-- SELECT result_strategy, ST_AsText(result_geom) FROM process_candidates(ARRAY[45, 46, 47, 123, 200], ARRAY[0.9, 0.85, 0.8, 0.7, 0.6]);
