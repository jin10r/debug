-- =============================================================================
-- process_candidates.sql
-- Логика обработки кандидатов в PostGIS:
-- 1. Дедупликация по геометрии
-- 2. Проверка пересечений
-- 3. Генерация геометрии (точка/полигон)
-- =============================================================================

-- =============================================================================
-- Функция process_candidates(p_street_ids, p_street_scores)
-- Вход: массив ID улиц и их scores
-- Выход: GEOMETRY и strategy
-- =============================================================================

CREATE OR REPLACE FUNCTION process_candidates(
    p_street_ids INT[] DEFAULT NULL,
    p_street_scores FLOAT[] DEFAULT NULL,
    p_pseudo_radius_meters FLOAT DEFAULT 100.0
)
RETURNS TABLE(
    result_geom GEOMETRY,
    result_strategy VARCHAR(40),
    result_matches JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_geom GEOMETRY;
    v_strategy VARCHAR(40);
    v_matches JSONB;
BEGIN
    -- =======================================================================
    -- СЦЕНАРИЙ 3: Нет кандидатов → random point в questionoverlay (Одесса)
    -- =======================================================================
    IF p_street_ids IS NULL OR array_length(p_street_ids, 1) = 0 THEN
        v_strategy := 'random';
        
        -- questionoverlay: случайная точка в Одессе
        -- Центр Одессы: 46.4825, 30.7233, радиус ~10км
        v_geom := ST_SetSRID(ST_MakePoint(
            30.7233 + 0.09 * (random() - 0.5),  -- ±0.09 градуса (~10км)
            46.4825 + 0.09 * (random() - 0.5)
        ), 4326);
        
        v_matches := '[]'::jsonb;
        RETURN QUERY SELECT v_geom, v_strategy, v_matches;
    END IF;

    -- =======================================================================
    -- Формируем matches из всех улиц с их скорингом
    -- =======================================================================
    IF p_street_scores IS NOT NULL 
       AND array_length(p_street_scores, 1) = array_length(p_street_ids, 1) THEN
        SELECT jsonb_agg(jsonb_build_object(
            'street_id', s.id,
            'name', s.names[1],
            'similarity', u.score,
            'matched_part', 'full_text'
        ) ORDER BY u.score DESC)
        INTO v_matches
        FROM streets s,
             unnest(p_street_ids, p_street_scores) AS u(id, score)
        WHERE s.id = u.id;
    ELSE
        v_matches := '[]'::jsonb;
    END IF;

    -- =======================================================================
    -- СЦЕНАРИЙ 2 (одна улица): Пересечение не найдено → полная геометрия лучшей
    -- =======================================================================
    IF array_length(p_street_ids, 1) = 1 THEN
        SELECT ST_MakeValid(geom) INTO v_geom
        FROM streets
        WHERE id = p_street_ids[1];

        v_strategy := 'single_match';
        RETURN QUERY SELECT v_geom, v_strategy, v_matches;
    END IF;

    -- =======================================================================
    -- СЦЕНАРИИ 1-2 (2+ улицы): Дедупликация + пересечения
    -- =======================================================================
    WITH street_geoms AS (
        -- Получаем все улицы с их геометриями
        -- ST_MakeValid защищает от невалидных геометрий (самопересечения и др.)
        SELECT
            s.id,
            s.names[1] as name,
            ST_MakeValid(s.geom) as geom,
            -- Хэш геометрии для группировки синонимов (дедупликация)
            ST_AsText(ST_SnapToGrid(ST_MakeValid(s.geom), 0.0001)) as geom_hash
        FROM streets s
        WHERE s.id = ANY(p_street_ids)
    ),
    -- Дедупликация: оставляем по одному представителю на уникальную геометрию
    unique_geoms AS (
        SELECT DISTINCT ON (geom_hash)
            id,
            name,
            geom
        FROM street_geoms
        ORDER BY geom_hash, id
    ),
    -- Точки ПЕРЕСЕЧЕНИЯ между РАЗНЫМИ геометриями
    intersections AS (
        SELECT
            ST_Intersection(a.geom, b.geom) as point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        WHERE a.id < b.id  -- Только РАЗНЫЕ объекты
          AND ST_IsValid(a.geom)
          AND ST_IsValid(b.geom)
          AND ST_Intersects(a.geom, b.geom)
          AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
          AND GeometryType(ST_Intersection(a.geom, b.geom)) = 'POINT'
    ),
    -- Точки ПСЕВДОПЕРЕСЕЧЕНИЯ (расстояние ≤ p_pseudo_radius_meters)
    pseudo_intersections AS (
        SELECT
            ST_Centroid(ST_Collect(
                ST_ClosestPoint(a.geom, b.geom),
                ST_ClosestPoint(b.geom, a.geom)
            )) as point
        FROM unique_geoms a
        CROSS JOIN unique_geoms b
        WHERE a.id < b.id  -- Только РАЗНЫЕ объекты
          AND ST_IsValid(a.geom)
          AND ST_IsValid(b.geom)
          AND ST_DWithin(
              ST_Transform(a.geom, 3857),
              ST_Transform(b.geom, 3857),
              p_pseudo_radius_meters
            )
    ),
    -- Объединяем все точки
    all_points AS (
        SELECT point FROM intersections
        UNION ALL
        SELECT point FROM pseudo_intersections
    ),
    -- Строим финальную геометрию
    final AS (
        SELECT
            CASE
                -- НЕТ точек → СЦЕНАРИЙ 2: полная геометрия улицы с ЛУЧШИМ SCORE
                WHEN (SELECT COUNT(*) FROM all_points WHERE point IS NOT NULL) = 0 THEN
                    (
                        SELECT ST_MakeValid(s.geom)
                        FROM streets s
                        INNER JOIN unnest(p_street_ids, COALESCE(p_street_scores, ARRAY_FILL(1.0, ARRAY[array_length(p_street_ids, 1)]))) AS u(id, score)
                            ON s.id = u.id
                        ORDER BY u.score DESC
                        LIMIT 1
                    )

                -- 1 точка → Point
                WHEN (SELECT COUNT(*) FROM all_points WHERE point IS NOT NULL) = 1 THEN
                    (SELECT point FROM all_points WHERE point IS NOT NULL LIMIT 1)

                -- 2+ точки → Polygon (ConvexHull)
                ELSE
                    ST_ConvexHull(ST_Collect(point))
            END as geom,
            CASE
                WHEN (SELECT COUNT(*) FROM all_points WHERE point IS NOT NULL) = 0 THEN 'single_match'
                WHEN (SELECT COUNT(*) FROM all_points WHERE point IS NOT NULL) = 1 THEN 'single_intersection'
                ELSE 'polygon_intersection'
            END as strategy
        FROM all_points
    )
    SELECT geom, strategy INTO v_geom, v_strategy
    FROM final;

    -- Fallback: если геометрия NULL → выбираем улицу с лучшим score
    IF v_geom IS NULL THEN
        SELECT ST_MakeValid(s.geom) INTO v_geom
        FROM streets s
        INNER JOIN unnest(p_street_ids, COALESCE(p_street_scores, ARRAY_FILL(1.0, ARRAY[array_length(p_street_ids, 1)]))) AS u(id, score)
            ON s.id = u.id
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

-- Тест 1: Нет кандидатов (Сценарий 3 - random)
-- SELECT * FROM process_candidates(NULL, NULL);

-- Тест 2: Одна улица без пересечений (Сценарий 2 - single_match)
-- SELECT * FROM process_candidates(ARRAY[45], ARRAY[0.8]);

-- Тест 3: Две улицы с пересечением (Сценарий 1 - intersection)
-- SELECT * FROM process_candidates(ARRAY[45, 123], ARRAY[0.9, 0.7]);

-- Тест 4: Две улицы БЕЗ пересечения (Сценарий 2 - single_match)
-- SELECT * FROM process_candidates(ARRAY[45, 200], ARRAY[0.9, 0.7]);

-- Тест 5: Много улиц с пересечениями (Сценарий 1 - polygon_intersection)
-- SELECT * FROM process_candidates(ARRAY[45, 46, 47, 123, 200], ARRAY[0.9, 0.85, 0.8, 0.7, 0.6]);