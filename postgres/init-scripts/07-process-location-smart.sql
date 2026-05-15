-- =============================================================================
-- 07-process-location-smart.sql
-- Умная функция обработки событий с дедупликацией по геометрии
-- ОБНОВЛЕННАЯ ВЕРСИЯ: возвращает ЛЮБЫЕ геометрии пересечений объектов
-- =============================================================================
-- Ключевые особенности:
-- 1. Дедупликация синонимов по геометрии (ST_SnapToGrid)
-- 2. ВСЕ пересечения между уникальными геометриями (любые типы)
-- 3. ВСЕ псевдопересечения (500м - увеличенный радиус)
-- 4. Возвращает оригинальные геометрии, а не центроиды
-- 5. Поддерживает POINT, LINESTRING, POLYGON, MULTI* пересечения

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
        'single_intersection',
        'full_intersection_geometry',
        'combined_geometries'
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
    p_street_scores FLOAT[] DEFAULT NULL,   -- Скоринг для каждой улицы
    p_matched_parts TEXT[] DEFAULT NULL,    -- Часть текста, совпавшая с улицей
    p_max_distance_meters FLOAT DEFAULT 2000.0,
    p_pseudo_radius_meters FLOAT DEFAULT 500.0
)
    RETURNS TABLE(
    event_id INT,
    result_layer TEXT,
    result_strategy VARCHAR(40),
    result_geom GEOMETRY,
    result_matches JSONB
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
    DECLARE
    v_layer TEXT := COALESCE(p_layer, 'pig');
    v_matches JSONB;
    v_strategy VARCHAR(40) := 'random';
    v_geom GEOMETRY;
    v_event_id INT;
BEGIN
    -- ПРОВЕРЯЕМ ВХОДНЫЕ ДАННЫЕ
    IF p_street_ids IS NULL OR array_length(p_street_ids, 1) = 0 THEN
        -- НЕТ улиц → случайная точка
        SELECT INTO v_geom ST_SetSRID(ST_MakePoint(
            30.71 + ((RANDOM() - 0.5) * 0.05), 
            46.45 + ((RANDOM() - 0.5) * 0.05)
        ), 4326);
        v_matches = jsonb_build_object('strategy', 'random', 'streets', '[]');
        
    ELSIF array_length(p_street_ids, 1) = 1 THEN
        -- ОДНА улица → возвращаем её геометрию
        SELECT INTO v_geom, v_strategy geom, 'single_match'
        FROM streets WHERE id = p_street_ids[1];
        
        v_matches = jsonb_build_object(
            'strategy', 'single_match',
            'streets', jsonb_build_array(jsonb_build_object(
                'id', p_street_ids[1],
                'score', COALESCE(p_street_scores[1], 1.0)
            ))
        );
        
    ELSE
        -- МНОГО улиц → ищем пересечения
        WITH 
        -- Сначала создаем unique_geoms как основной CTE с сохранением данных
        unique_geoms AS (
            -- Дедупликация по геометрии (ST_SnapToGrid)
            SELECT DISTINCT ON (ST_SnapToGrid(geom, 0.001))
                id, geom, names
            FROM streets 
            WHERE id = ANY(p_street_ids)
            ORDER BY ST_SnapToGrid(geom, 0.001), id
        ),
        intersections AS (
            -- ВСЕ возможные пересечения между уникальными геометриями
            SELECT 
                a.id as id1, 
                b.id as id2,
                ST_Intersection(a.geom, b.geom) as intersection_geom
            FROM unique_geoms a
            CROSS JOIN unique_geoms b
            WHERE a.id < b.id  -- избегаем self-join и дубликатов
            AND NOT ST_IsEmpty(ST_Intersection(a.geom, b.geom))
        ),
        pseudo_intersections AS (
            -- ВСЕ псевдопересечения (на основе ST_DWithin с увеличенным радиусом)
            SELECT 
                a.id as id1,
                b.id as id2,
                ST_Centroid(ST_Collect(ARRAY[a.geom, b.geom])) as pseudo_intersection_geom
            FROM unique_geoms a
            CROSS JOIN unique_geoms b
            WHERE a.id < b.id
            AND ST_DWithin(a.geom, b.geom, p_pseudo_radius_meters)
        ),
        valid_geoms AS (
            -- Валидные пересечения
            SELECT intersection_geom as geom
            FROM intersections
            WHERE NOT ST_IsEmpty(intersection_geom)
            UNION ALL
            -- Валидные псевдопересечения
            SELECT pseudo_intersection_geom as geom
            FROM pseudo_intersections
            WHERE NOT ST_IsEmpty(pseudo_intersection_geom)
        ),
        strategy_result AS (
            -- ВЫБИРАЕМ итоговую стратегию и геометрию
            SELECT 
                CASE 
                    WHEN COUNT(*) > 1 THEN 'combined_geometries'
                    WHEN COUNT(*) = 1 THEN 'full_intersection_geometry'
                    ELSE 'single_match'
                END as strategy,
                CASE 
                    WHEN COUNT(*) > 0 THEN 
                        ST_Union(geom)
                    ELSE 
                        (SELECT geom FROM streets WHERE id = p_street_ids[1])
                END as geometry
            FROM valid_geoms
        )
        
        -- Получаем результаты
        SELECT strategy, geometry
        INTO v_strategy, v_geom
        FROM strategy_result;

        -- Строим результат matches (теперь с корректной видимостью unique_geoms)
        WITH ug AS (
            SELECT DISTINCT ON (ST_SnapToGrid(geom, 0.001))
                id, geom, names
            FROM streets 
            WHERE id = ANY(p_street_ids)
            ORDER BY ST_SnapToGrid(geom, 0.001), id
        )
        SELECT jsonb_build_object(
            'strategy', v_strategy,
            'streets', (
                SELECT jsonb_agg(jsonb_build_object(
                    'id', ug.id,
                    'names', ug.names,
                    'score', COALESCE(p_street_scores[array_position(p_street_ids, ug.id)], 1.0)
                ))
                FROM ug
            )
        ) INTO v_matches;
    END IF;

    -- ФИЛЬТРАЦИЯ выбросов (геометрия слишком далеко от центроида)
    IF v_geom IS NOT NULL AND NOT ST_IsEmpty(v_geom) THEN
        DECLARE
            v_centroid GEOMETRY := ST_Centroid(v_geom);
            v_distances FLOAT[];
        BEGIN
            -- Пропускаем фильтрацию для объединенных геометрий
            IF v_strategy != 'combined_geometries' AND 
               ST_Distance(v_centroid, v_geom, true) > p_max_distance_meters THEN
                -- Выброс → используем случайную точку в центроиде
                v_geom := v_centroid;
                v_strategy := 'random';
            END IF;
        END;
    END IF;

    -- СОЗДАЁМ событие
    INSERT INTO events (event_time, description, layer, photo_url, strategy, geom)
    VALUES (p_event_time, p_description, v_layer, p_photo_url, v_strategy, v_geom)
    RETURNING id INTO v_event_id;

    -- Возвращаем результат
    event_id := v_event_id;
    result_layer := v_layer;
    result_strategy := v_strategy;
    result_geom := v_geom;
    result_matches := v_matches;
    
    RETURN NEXT;
END;
$$;

-- Комментарий для функции
COMMENT ON FUNCTION process_location_smart IS 
'Умная функция обработки геопространственных событий с дедупликацией и поиском пересечений';

-- Индексы для оптимизации
CREATE INDEX IF NOT EXISTS idx_streets_geom_gist ON streets USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_streets_id ON streets (id);