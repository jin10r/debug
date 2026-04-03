-- =============================================================================
-- 07-process-location-smart.sql
-- Умная функция обработки событий с дедупликацией по геометрии
-- =============================================================================
-- Ключевые особенности:
-- 1. Дедупликация синонимов по геометрии (ST_SnapToGrid)
-- 2. ВСЕ пересечения между уникальными геометрия
-- 3. ВСЕ псевдопересечения (100м)
-- 4. Фильтрация выбросов (≤2км от центроида)
-- 5. Построение геометрии (Point/LineString/Polygon)
-- 6. Проверка размера (диагональ ≤2км)

-- =============================================================================
-- Обновление ограничения strategy в таблице events
-- =============================================================================

ALTER TABLE events DROP CONSTRAINT IF EXISTS events_strategy_check;
ALTER TABLE events ADD CONSTRAINT events_strategy_check CHECK (
    strategy::text IN (
        'random',
        'single_match',
        'intersection',
        'polygon_intersection',
        'single_intersection'
    )
);

-- =============================================================================
-- Функция process_location_smart()
-- =============================================================================

CREATE OR REPLACE FUNCTION process_location_smart(
    p_event_time TIMESTAMPTZ,
    p_description TEXT,
    p_layer TEXT,
    p_photo_url TEXT DEFAULT NULL,
    p_street_ids INT[] DEFAULT NULL,
    p_street_scores FLOAT[] DEFAULT NULL,  -- Скоринг для каждой улицы
    p_max_distance_meters FLOAT DEFAULT 2000.0,
    p_pseudo_radius_meters FLOAT DEFAULT 100.0
)
RETURNS TABLE(
    event_id INT,
    result_layer TEXT,
    result_strategy VARCHAR(20),
    result_geom GEOMETRY,
    result_matches JSONB
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_geom GEOMETRY;
    v_strategy VARCHAR(20);
    v_matches JSONB;
    v_event_id INT;
    v_diagonal_meters FLOAT;
BEGIN
    -- 1. Формируем matches из ВСЕХ найденных улиц с их скорингом
    -- Используем unnest с двумя массивами вместе для правильного соответствия индексов
    IF p_street_scores IS NOT NULL AND array_length(p_street_scores, 1) = array_length(p_street_ids, 1) THEN
        -- Используем переданный скоринг с правильным сопоставлением
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
        -- Fallback: все similarity = 1.0
        SELECT jsonb_agg(jsonb_build_object(
            'street_id', s.id,
            'name', s.names[1],
            'similarity', 1.0,
            'matched_part', 'full_text'
        )) INTO v_matches
        FROM streets s
        WHERE s.id = ANY(p_street_ids);
    END IF;

    -- 2. Если улиц нет → random в question_overlay
    IF p_street_ids IS NULL OR array_length(p_street_ids, 1) = 0 THEN
        v_strategy := 'random';
        v_geom := ST_SetSRID(ST_MakePoint(
            30.83135 + 0.045 * sqrt(random()) * cos(2.0 * pi() * random()),
            46.49804 + 0.045 * sqrt(random()) * sin(2.0 * pi() * random())
        ), 4326);

    -- 3. Если одна улица → полная геометрия (уже POLYGON или LINESTRING из CSV)
    ELSIF array_length(p_street_ids, 1) = 1 THEN
        SELECT geom INTO v_geom
        FROM streets
        WHERE id = p_street_ids[1];
        v_strategy := 'single_match';

    -- 4. Если 2+ улицы → дедупликация по геометрии + пересечения
    ELSE
        WITH street_geoms AS (
            -- Получаем все улицы с их геометриями
            SELECT 
                s.id,
                s.names[1] as name,
                s.geom,
                -- Хэш геометрии для группировки синонимов
                ST_AsText(ST_SnapToGrid(s.geom, 0.0001)) as geom_hash
            FROM streets s
            WHERE s.id = ANY(p_street_ids)
        ),
        -- ДЕДУПЛИКАЦИЯ по геометрии: оставляем по одному представителю на уникальную геометрию
        unique_geoms AS (
            SELECT DISTINCT ON (geom_hash)
                id,
                name,
                geom
            FROM street_geoms
            ORDER BY geom_hash, id  -- Берём первый по ID (детерминировано)
        ),
        -- ВСЕ точки пересечения между РАЗНЫМИ геометрия
        all_intersections AS (
            SELECT 
                ST_Intersection(a.geom, b.geom) as point
            FROM unique_geoms a
            CROSS JOIN unique_geoms b
            WHERE a.id < b.id
              AND ST_Intersects(a.geom, b.geom)
              AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
              AND GeometryType(ST_Intersection(a.geom, b.geom)) = 'POINT'
        ),
        -- ВСЕ точки псевдопересечения (100м) между РАЗНЫМИ геометрия
        all_pseudo AS (
            SELECT
                ST_Centroid(ST_Collect(
                    ST_ClosestPoint(a.geom, b.geom),
                    ST_ClosestPoint(b.geom, a.geom)
                )) as point
            FROM unique_geoms a
            CROSS JOIN unique_geoms b
            WHERE a.id < b.id
              AND ST_DWithin(
                  ST_Transform(a.geom, 3857),
                  ST_Transform(b.geom, 3857),
                  p_pseudo_radius_meters
                )
        ),
        -- Объединяем все точки
        all_points AS (
            SELECT point FROM all_intersections
            UNION ALL
            SELECT point FROM all_pseudo
        ),
        -- Вычисляем центроид всех точек
        centroid_calc AS (
            SELECT ST_Centroid(ST_Collect(point)) as centroid
            FROM all_points
            WHERE point IS NOT NULL
        ),
        -- Фильтрация выбросов: расстояние от центроида ≤ max_distance
        filtered_points AS (
            SELECT 
                p.point
            FROM all_points p, centroid_calc c
            WHERE p.point IS NOT NULL
              AND ST_Distance(
                  ST_Transform(p.point, 3857),
                  ST_Transform(c.centroid, 3857)
              ) <= p_max_distance_meters
        ),
        -- Собираем отфильтрованные точки
        collected_points AS (
            SELECT ST_Collect(point) as collected
            FROM filtered_points
        ),
        -- Строим геометрию
        final_geometry AS (
            SELECT
                CASE
                    -- 0 точек пересечения → берём полную геометрию ЛУЧШЕЙ улицы (первый в p_street_ids)
                    WHEN (SELECT COUNT(*) FROM filtered_points) = 0 THEN
                        (SELECT geom FROM streets WHERE id = p_street_ids[1])

                    -- 1 точка → Point
                    WHEN (SELECT COUNT(*) FROM filtered_points) = 1 THEN
                        (SELECT point FROM filtered_points LIMIT 1)

                    -- 2+ точки → Convex Hull
                    ELSE
                        ST_ConvexHull(collected)
                END as geom,
                CASE
                    WHEN (SELECT COUNT(*) FROM filtered_points) = 0 THEN 'single_match'
                    WHEN (SELECT COUNT(*) FROM filtered_points) = 1 THEN 'single_intersection'
                    ELSE 'polygon_intersection'
                END as strategy
            FROM collected_points
        )
        SELECT geom, strategy INTO v_geom, v_strategy FROM final_geometry;

        -- 4. Проверка размера полигона (диагональ ≤ 2км)
        IF ST_GeometryType(v_geom) = 'ST_Polygon' THEN
            SELECT 
                ST_Distance(
                    ST_Transform(ST_PointN(ST_ExteriorRing(v_geom), 1), 3857),
                    ST_Transform(ST_PointN(ST_ExteriorRing(v_geom), 3), 3857)
                ) INTO v_diagonal_meters;
            
            IF v_diagonal_meters > p_max_distance_meters THEN
                -- Полигон слишком большой → берём центроид
                v_geom := ST_Centroid(v_geom);
                v_strategy := 'single_match';
            END IF;
        END IF;

        -- 5. Fallback: если геометрия всё ещё NULL
        IF v_geom IS NULL THEN
            SELECT geom INTO v_geom
            FROM unique_geoms
            ORDER BY id
            LIMIT 1;
            v_strategy := 'single_match';
        END IF;
    END IF;

    -- 7. INSERT INTO events
    INSERT INTO events (event_time, description, photo_url, layer, matches, strategy, geom)
    VALUES (p_event_time, p_description, p_photo_url, p_layer, v_matches, v_strategy, v_geom)
    RETURNING id INTO v_event_id;

    -- 8. Обновление events_meta
    UPDATE events_meta
    SET version = version + 1,
        updated_at = now(),
        max_event_id = v_event_id
    WHERE id = 1;

    -- 9. NOTIFY для WebSocket
    PERFORM pg_notify('events_new', jsonb_build_object(
        'type', 'Feature',
        'geometry', ST_AsGeoJSON(v_geom)::jsonb,
        'properties', jsonb_build_object(
            'id', v_event_id,
            'layer', p_layer,
            'strategy', v_strategy,
            'description', p_description,
            'photo_url', p_photo_url,
            'matches', v_matches,
            'time', p_event_time
        )
    )::text);

    RETURN QUERY SELECT v_event_id, p_layer, v_strategy, v_geom, v_matches;
END;
$$;

COMMENT ON FUNCTION process_location_smart IS
    'Умная обработка событий с дедупликацией по геометрии, фильтрацией выбросов и построением геометрии';

-- =============================================================================
-- Примеры использования:
--
-- -- Тест 1: Синонимы (45, 46, 47 — одна геометрия)
-- SELECT * FROM process_location_smart(
--     now(), 'пушкинской левицкого', 'pig', NULL, 
--     ARRAY[45, 46, 47, 123]
-- );
--
-- -- Тест 2: Все синонимы
-- SELECT * FROM process_location_smart(
--     now(), 'пушкинской ул пушкина улица пушкина', 'pig', NULL, 
--     ARRAY[45, 46, 47]
-- );
--
-- -- Тест 3: Нет пересечений
-- SELECT * FROM process_location_smart(
--     now(), 'пушкинской левицкого катаева', 'pig', NULL, 
--     ARRAY[45, 123, 200]
-- );
-- =============================================================================
