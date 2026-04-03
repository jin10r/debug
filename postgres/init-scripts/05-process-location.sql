-- Migration: 011_simplified_process_location.sql
-- Упрощённая функция обработки событий
-- Парсер передаёт готовые street_ids, PostgreSQL только определяет геометрию

-- =============================================================================
-- Функция process_location()
-- =============================================================================

CREATE OR REPLACE FUNCTION process_location(
    p_event_time TIMESTAMPTZ,
    p_description TEXT,
    p_layer TEXT,
    p_photo_url TEXT DEFAULT NULL,
    p_street_ids INT[] DEFAULT NULL
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
    v_layer TEXT := COALESCE(p_layer, 'pig');
    v_matches JSONB;
    v_strategy VARCHAR(20) := 'random';
    v_geom GEOMETRY;
    v_event_id INT;
    v_buffer_radius FLOAT := 100.0;
BEGIN
    -- 1. Если парсер нашёл улицы - используем их
    IF p_street_ids IS NOT NULL AND array_length(p_street_ids, 1) > 0 THEN
        -- Формируем JSONB из найденных улиц (берем первое имя из массива как основное)
        SELECT jsonb_agg(jsonb_build_object(
            'street_id', s.id,
            'name', s.names[1],
            'similarity', 1.0,
            'matched_part', 'full_text'
        ) ORDER BY s.id)
        INTO v_matches
        FROM streets s
        WHERE s.id = ANY(p_street_ids);

        -- 2. Геопространственные вычисления
        IF array_length(p_street_ids, 1) = 1 THEN
            -- Одна улица - берём геометрию
            SELECT geom INTO v_geom
            FROM streets
            WHERE id = p_street_ids[1];
            v_strategy := 'single_match';

        ELSIF array_length(p_street_ids, 1) >= 2 THEN
            -- 2+ улицы - ищем пересечение
            WITH found_objects AS (
                SELECT s.id, s.geom
                FROM streets s
                WHERE s.id = ANY(p_street_ids)
            ),
            intersections AS (
                SELECT
                    a.id as id1,
                    b.id as id2,
                    ST_Intersection(a.geom, b.geom) as intersection_geom,
                    ST_DWithin(
                        ST_Transform(a.geom, 3857),
                        ST_Transform(b.geom, 3857),
                        v_buffer_radius
                    ) as is_nearby,
                    ST_ClosestPoint(a.geom, b.geom) as closest_a,
                    ST_ClosestPoint(b.geom, a.geom) as closest_b
                FROM found_objects a
                CROSS JOIN found_objects b
                WHERE a.id < b.id
            ),
            first_connection AS (
                SELECT
                    CASE
                        -- Есть настоящее пересечение
                        WHEN NOT ST_IsEmpty(intersection_geom) AND intersection_geom IS NOT NULL THEN
                            ST_PointOnSurface(intersection_geom)
                        -- Улицы рядом - берём центр между ближайшими точками
                        WHEN is_nearby THEN
                            ST_Centroid(ST_Collect(closest_a, closest_b))
                        ELSE NULL
                    END AS connection_geom
                FROM intersections
                LIMIT 1
            )
            SELECT connection_geom INTO v_geom
            FROM first_connection
            WHERE connection_geom IS NOT NULL;

            IF v_geom IS NOT NULL AND NOT ST_IsEmpty(v_geom) THEN
                v_strategy := 'intersection';
            ELSE
                -- Нет пересечения - берём первую улицу
                SELECT geom INTO v_geom
                FROM streets
                WHERE id = p_street_ids[1];
                v_strategy := 'single_match';
            END IF;
        END IF;

    -- 3. Fallback: случайная точка
    ELSE
        v_matches := '[]'::JSONB;
        v_strategy := 'random';
        -- Случайная точка в пределах bounds (Одесса)
        v_geom := ST_SetSRID(ST_MakePoint(
            30.83135 + 0.045 * sqrt(random()) * cos(2.0 * pi() * random()),
            46.49804 + 0.045 * sqrt(random()) * sin(2.0 * pi() * random())
        ), 4326);
    END IF;

    -- 4. Вставка события
    INSERT INTO events (
        event_time,
        description,
        photo_url,
        layer,
        matches,
        strategy,
        geom
    )
    VALUES (
        p_event_time,
        p_description,
        p_photo_url,
        v_layer,
        v_matches,
        v_strategy,
        v_geom
    )
    RETURNING id INTO v_event_id;

    -- 5. Обновление мета-данных
    UPDATE events_meta
    SET version = version + 1,
        updated_at = now()
    WHERE id = 1;

    -- 6. NOTIFY для WebSocket
    PERFORM pg_notify('events_new', jsonb_build_object(
        'type', 'Feature',
        'geometry', ST_AsGeoJSON(v_geom)::jsonb,
        'properties', jsonb_build_object(
            'id', v_event_id,
            'layer', v_layer,
            'strategy', v_strategy,
            'description', p_description,
            'photo_url', p_photo_url,
            'matches', v_matches,
            'time', p_event_time
        )
    )::text);

    RETURN QUERY
    SELECT v_event_id, v_layer, v_strategy, v_geom, v_matches;
END;
$$;

COMMENT ON FUNCTION process_location IS
    'Обработка событий с готовыми street_ids от парсера (SQLite + rapidfuzz)';

-- =============================================================================
-- Индексы для оптимизации
-- =============================================================================

-- Индекс для быстрого поиска по street_ids
CREATE INDEX IF NOT EXISTS idx_events_matches_street_id
    ON events USING GIN ((matches->'street_id'));

-- =============================================================================
-- Примеры использования:
--
-- SELECT * FROM process_location(
--     now(),
--     'перекрытие на пушкинской и ленина',
--     'traffic',
--     NULL,
--     ARRAY[123, 456]
-- );
-- =============================================================================
