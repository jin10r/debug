-- 03-functions.sql
-- Единая функция обработки событий с двухстадийным поиском (pg_trgm)
-- 1. Прием очищенного текста и слоя -> 2. Фильтрация стоп-слов -> 3. Двухстадийный поиск -> 4. Геометрия

CREATE OR REPLACE FUNCTION process_event(
    p_event_time TIMESTAMPTZ,
    p_description TEXT,  -- уже очищенный текст из парсера
    p_layer TEXT,        -- определенный слой из парсера
    p_photo_url TEXT DEFAULT NULL
)
RETURNS TABLE(event_id INT, layer TEXT, strategy VARCHAR(20), geom GEOMETRY)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_layer TEXT := COALESCE(p_layer, 'pig');
    v_matches JSONB;
    v_strategy VARCHAR(20) := 'random';
    v_geom GEOMETRY;
    v_event_id INT;
    v_street_ids INT[];
    v_best_street_id INT;
    v_best_score FLOAT;
    v_buffer_radius FLOAT := 100.0;  -- метры для псевдопересечения
    v_similarity_threshold FLOAT := 0.67;
    v_stage1_threshold FLOAT;
    v_stage2_threshold FLOAT;
    v_min_word_length INT := 4;
    v_max_matches INT := 5;
    v_description_normalized TEXT;
BEGIN
    -- Нормализация текста (украинские буквы → русские)
    v_description_normalized := translate(lower(p_description), 'ієїє', 'иеее');
    
    -- Пороги для двух стадий
    v_stage1_threshold := v_similarity_threshold * 0.8;
    v_stage2_threshold := v_similarity_threshold;
    
    -- 1. ИЗВЛЕЧЕНИЕ СЛОВ из очищенного текста
    WITH extracted_words AS (
        SELECT DISTINCT trim(w) AS word
        FROM unnest(string_to_array(v_description_normalized, ' ')) AS w
        WHERE length(trim(w)) >= v_min_word_length
          AND NOT EXISTS (SELECT 1 FROM stopwords s WHERE s.word = trim(w))
    ),
    
    -- Stage 1: ПОИСК ПО ОТДЕЛЬНЫМ СЛОВАМ (по всем синонимам names)
    stage1_matches AS (
        SELECT DISTINCT ON (s.id)
            s.id,
            s.names[1] as name,
            s.geom AS street_geom,
            CASE 
                WHEN ew.word = lower(n.name) THEN 1.0
                WHEN lower(n.name) LIKE '%' || ew.word || '%' THEN 
                    0.8 + (0.2 * (length(ew.word)::float / length(n.name)::float))
                WHEN ew.word % lower(n.name) THEN GREATEST(similarity(ew.word, lower(n.name)), 0.5)
                ELSE similarity(ew.word, lower(n.name))
            END AS sim_score,
            ew.word AS matched_part,
            1 AS stage
        FROM extracted_words ew
        JOIN streets s ON (
            EXISTS (SELECT 1 FROM unnest(s.names) AS n WHERE 
                ew.word % lower(n)
                OR lower(n) LIKE '%' || ew.word || '%'
            )
        )
        WHERE EXISTS (SELECT 1 FROM unnest(s.names) AS n WHERE 
            similarity(ew.word, lower(n)) >= v_stage1_threshold
            OR lower(n) LIKE '%' || ew.word || '%'
        )
        ORDER BY s.id, 
                 CASE 
                     WHEN ew.word = lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)) THEN 1.0
                     WHEN lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)) LIKE '%' || ew.word || '%' THEN 0.9
                     ELSE similarity(ew.word, lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)))
                 END DESC,
                 length((SELECT n FROM unnest(s.names) AS n LIMIT 1)) DESC
    ),
    
    -- Stage 2: ПОИСК ПО КОМБИНАЦИЯМ СЛОВ
    word_array AS (
        SELECT array_agg(word ORDER BY ord) AS words
        FROM (
            SELECT word, row_number() over () AS ord
            FROM extracted_words
        ) w
    ),
    combinations AS (
        SELECT CASE 
            WHEN array_length(wa.words, 1) >= 2 
            THEN wa.words[i] || ' ' || wa.words[i+1]
            ELSE NULL
        END AS combo
        FROM word_array wa,
             generate_series(1, GREATEST(array_length(wa.words, 1) - 1, 0)) AS i
        WHERE wa.words IS NOT NULL
    ),
    stage2_matches AS (
        SELECT DISTINCT ON (s.id)
            s.id,
            s.names[1] as name,
            s.geom AS street_geom,
            CASE 
                WHEN c.combo = lower(n.name) THEN 1.0
                WHEN lower(n.name) LIKE '%' || c.combo || '%' THEN 
                    0.8 + (0.2 * (length(c.combo)::float / length(n.name)::float))
                WHEN c.combo % lower(n.name) THEN GREATEST(similarity(c.combo, lower(n.name)), 0.5)
                ELSE similarity(c.combo, lower(n.name))
            END AS sim_score,
            c.combo AS matched_part,
            2 AS stage
        FROM combinations c
        JOIN streets s ON EXISTS (SELECT 1 FROM unnest(s.names) AS n WHERE 
            c.combo % lower(n) OR lower(n) LIKE '%' || c.combo || '%'
        )
        WHERE c.combo IS NOT NULL
          AND EXISTS (SELECT 1 FROM unnest(s.names) AS n WHERE 
              similarity(c.combo, lower(n)) >= v_stage2_threshold
              OR lower(n) LIKE '%' || c.combo || '%'
          )
        ORDER BY s.id, 
                 CASE 
                     WHEN c.combo = lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)) THEN 1.0
                     WHEN lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)) LIKE '%' || c.combo || '%' THEN 0.9
                     ELSE similarity(c.combo, lower((SELECT n FROM unnest(s.names) AS n LIMIT 1)))
                 END DESC,
                 length((SELECT n FROM unnest(s.names) AS n LIMIT 1)) DESC
    ),
    
    -- Объединение всех совпадений с приоритетом stage 2
    all_matches AS (
        SELECT id, name, street_geom AS geom, sim_score, matched_part, stage
        FROM stage2_matches
        UNION ALL
        SELECT id, name, street_geom AS geom, sim_score, matched_part, stage
        FROM stage1_matches sm
        WHERE sm.id NOT IN (SELECT s2.id FROM stage2_matches s2)
    ),
    
    -- Дедупликация и выбор лучших
    ranked_matches AS (
        SELECT DISTINCT ON (am.id)
            am.id,
            am.name,
            am.geom AS match_geom,
            am.sim_score,
            am.matched_part,
            ROW_NUMBER() OVER (ORDER BY am.sim_score DESC, am.stage DESC) AS rank
        FROM all_matches am
        ORDER BY am.id, am.sim_score DESC, am.stage DESC
    ),
    
    final_matches AS (
        SELECT id, name, match_geom AS geom, sim_score, matched_part
        FROM ranked_matches
        WHERE rank <= v_max_matches
        ORDER BY sim_score DESC
    )
    
    SELECT
        array_agg(id ORDER BY sim_score DESC),
        jsonb_agg(jsonb_build_object(
            'street_id', id,
            'name', name,
            'similarity', sim_score,
            'matched_part', matched_part
        ) ORDER BY sim_score DESC),
        (SELECT id FROM final_matches ORDER BY sim_score DESC LIMIT 1),
        (SELECT sim_score FROM final_matches ORDER BY sim_score DESC LIMIT 1)
    INTO v_street_ids, v_matches, v_best_street_id, v_best_score
    FROM final_matches;
    
    -- 3. ОПРЕДЕЛЕНИЕ ГЕОМЕТРИИ
    IF v_street_ids IS NULL OR array_length(v_street_ids, 1) = 0 THEN
        -- Нет совпадений - случайная точка в круге в пределах bounds
        v_strategy := 'random';
        -- Используем generate_random_location_v2 с дефолтными параметрами (круг вписанный в bounds)
        v_geom := ST_SetSRID(ST_MakePoint(
            30.83135 + 0.045 * sqrt(random()) * cos(2.0 * pi() * random()),
            46.49804 + 0.045 * sqrt(random()) * sin(2.0 * pi() * random())
        ), 4326);
    ELSIF array_length(v_street_ids, 1) = 1 THEN
        -- Одна улица - берем её геометрию
        SELECT s.geom INTO v_geom
        FROM streets s
        WHERE s.id = v_street_ids[1];
        v_strategy := 'single_match';
    ELSE
        -- Несколько улиц - ищем пересечения и псевдопересечения
        WITH found_objects AS (
            SELECT s.id, s.geom AS obj_geom, s.names[1] as name
            FROM streets s
            WHERE s.id = ANY(v_street_ids)
        ),
        -- Проверяем пересечения и объекты в радиусе 100м
        proximity_check AS (
            SELECT 
                a.id as id1, 
                b.id as id2,
                ST_Intersects(a.obj_geom, b.obj_geom) as has_intersection,
                ST_DWithin(
                    ST_Transform(a.obj_geom, 3857), 
                    ST_Transform(b.obj_geom, 3857), 
                    v_buffer_radius
                ) as has_pseudo
            FROM found_objects a
            CROSS JOIN found_objects b
            WHERE a.id < b.id
        ),
        -- Находим первое пересечение или псевдопересечение
        first_connection AS (
            SELECT 
                id1, 
                id2,
                CASE 
                    WHEN has_intersection THEN 'intersection'::VARCHAR(20)
                    WHEN has_pseudo THEN 'intersection'::VARCHAR(20)
                END as connection_type,
                CASE 
                    WHEN has_intersection THEN 
                        ST_PointOnSurface(ST_Intersection(fo1.obj_geom, fo2.obj_geom))
                    WHEN has_pseudo THEN 
                        -- Центр между ближайшими точками
                        ST_Centroid(ST_Collect(
                            ST_ClosestPoint(fo1.obj_geom, fo2.obj_geom),
                            ST_ClosestPoint(fo2.obj_geom, fo1.obj_geom)
                        ))
                END as connection_geom
            FROM proximity_check pc
            JOIN found_objects fo1 ON fo1.id = pc.id1
            JOIN found_objects fo2 ON fo2.id = pc.id2
            WHERE has_intersection OR has_pseudo
            LIMIT 1
        )
        SELECT 
            connection_type,
            connection_geom
        INTO v_strategy, v_geom
        FROM first_connection;
        
        -- Если нет пересечений/псевдопересечений - берем геометрию лучшего объекта
        IF v_geom IS NULL THEN
            SELECT s.geom INTO v_geom
            FROM streets s
            WHERE s.id = v_best_street_id;
            v_strategy := 'single_match';
        END IF;
    END IF;
    
    -- 4. Вставка события
    INSERT INTO events (event_time, description, photo_url, layer, matches, strategy, geom)
    VALUES (p_event_time, p_description, p_photo_url, v_layer, 
            v_matches, v_strategy, v_geom)
    RETURNING id INTO v_event_id;
    
    -- 5. Обновление events_meta
    UPDATE events_meta 
    SET version = version + 1,
        updated_at = now(),
        max_event_id = v_event_id
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
    
    RETURN QUERY SELECT v_event_id, v_layer, v_strategy, v_geom;
END;
$$;

-- =============================================================================
-- Функция поиска локаций для API (без создания события)
-- =============================================================================
-- Двухстадийный поиск: Stage 1 (слова, вес 0.8), Stage 2 (комбинации, вес 1.0)

CREATE OR REPLACE FUNCTION search_locations(
    search_query TEXT,
    similarity_threshold FLOAT DEFAULT 0.67,
    max_matches INT DEFAULT 5
)
RETURNS JSONB AS $$
DECLARE
    matches JSONB := '[]'::JSONB;
    top_matches JSONB := '[]'::JSONB;
    intersection_result GEOMETRY;
    match_count INT;
    street1_id INT;
    street2_id INT;
    v_min_word_length INT := 4;
    v_buffer_radius FLOAT := 100.0;
    v_street_geom GEOMETRY;
    v_centroid GEOMETRY;
    v_query_normalized TEXT;
    v_stage1_threshold FLOAT;
    v_stage2_threshold FLOAT;
BEGIN
    -- Нормализация запроса (украинские буквы → русские)
    v_query_normalized := lower(regexp_replace(search_query, '<[^>]+>', '', 'g'));
    v_query_normalized := translate(v_query_normalized, 'ієїє', 'иеее');
    
    -- Пороги для двух стадий (legacy: stage 1 = 0.8×, stage 2 = 1.0×)
    v_stage1_threshold := similarity_threshold * 0.8;
    v_stage2_threshold := similarity_threshold;
    
    -- Stage 1: Поиск по отдельным словам
    WITH extracted_words AS (
        SELECT DISTINCT word
        FROM (
            SELECT regexp_split_to_table(
                v_query_normalized,
                '[^a-zа-яё0-9]+'
            ) AS word
        ) w
        WHERE length(word) >= v_min_word_length
          AND NOT EXISTS (SELECT 1 FROM stopwords s WHERE s.word = w.word)
    ),
    stage1_matches AS (
        SELECT DISTINCT ON (s.id)
            s.id,
            s.name,
            s.geom AS street_geom,
            CASE
                WHEN ew.word = lower(s.name) THEN 1.0
                WHEN ew.word % s.name THEN GREATEST(similarity(ew.word, s.name), 0.7)
                ELSE similarity(ew.word, s.name)
            END AS sim_score,
            ew.word AS matched_part
        FROM extracted_words ew
        JOIN streets s ON ew.word % s.name
        WHERE similarity(ew.word, s.name) >= v_stage1_threshold
        ORDER BY s.id,
                 CASE WHEN ew.word = lower(s.name) THEN 1.0 ELSE similarity(ew.word, s.name) END DESC
    ),
    -- Stage 2: Поиск по комбинациям слов (пары)
    word_array AS (
        SELECT array_agg(word ORDER BY ord) AS words
        FROM (
            SELECT word, row_number() over () AS ord
            FROM extracted_words
        ) w
    ),
    combinations AS (
        SELECT CASE 
            WHEN array_length(wa.words, 1) >= 2 
            THEN wa.words[i] || ' ' || wa.words[i+1]
            ELSE NULL
        END AS combo
        FROM word_array wa,
             generate_series(1, GREATEST(array_length(wa.words, 1) - 1, 0)) AS i
        WHERE wa.words IS NOT NULL
    ),
    stage2_matches AS (
        SELECT DISTINCT ON (s.id)
            s.id,
            s.name,
            s.geom AS street_geom,
            CASE
                WHEN c.combo = lower(s.name) THEN 1.0
                WHEN c.combo % s.name THEN GREATEST(similarity(c.combo, s.name), 0.7)
                ELSE similarity(c.combo, s.name)
            END AS sim_score,
            c.combo AS matched_part
        FROM combinations c
        JOIN streets s ON c.combo % s.name
        WHERE c.combo IS NOT NULL 
          AND similarity(c.combo, s.name) >= v_stage2_threshold
        ORDER BY s.id,
                 CASE WHEN c.combo = lower(s.name) THEN 1.0 ELSE similarity(c.combo, s.name) END DESC
    ),
    -- Объединение всех совпадений с приоритетом stage 2
    all_matches AS (
        SELECT id, name, street_geom AS geom, sim_score, matched_part, 2 AS stage
        FROM stage2_matches
        UNION ALL
        SELECT id, name, street_geom AS geom, sim_score, matched_part, 1 AS stage
        FROM stage1_matches sm
        WHERE sm.id NOT IN (SELECT s2.id FROM stage2_matches s2)
    ),
    -- Дедупликация и выбор лучших
    ranked_matches AS (
        SELECT DISTINCT ON (id)
            id,
            name,
            geom AS match_geom,
            sim_score,
            matched_part,
            ROW_NUMBER() OVER (ORDER BY sim_score DESC, stage DESC) AS rank
        FROM all_matches
        ORDER BY id, sim_score DESC, stage DESC
    ),
    final_matches AS (
        SELECT id, name, match_geom AS geom, sim_score, matched_part
        FROM ranked_matches
        WHERE rank <= max_matches
        ORDER BY sim_score DESC
    )
    SELECT 
        jsonb_agg(jsonb_build_object(
            'street_id', id,
            'name', name,
            'similarity', sim_score,
            'matched_part', matched_part
        ) ORDER BY sim_score DESC)
    INTO matches
    FROM final_matches;
    
    -- Проверка на пустые результаты
    IF matches IS NULL OR jsonb_array_length(matches) = 0 THEN
        RETURN jsonb_build_object(
            'success', FALSE, 
            'strategy', 'no_match', 
            'matches', '[]'::JSONB, 
            'geometry', NULL, 
            'name', NULL,
            'coordinates', NULL
        );
    END IF;
    
    -- Фильтрация по порогу
    SELECT jsonb_agg(m ORDER BY (m->>'similarity')::FLOAT DESC) 
    INTO top_matches
    FROM jsonb_array_elements(matches) AS m 
    WHERE (m->>'similarity')::FLOAT >= similarity_threshold;
    
    IF top_matches IS NULL OR jsonb_array_length(top_matches) = 0 THEN
        RETURN jsonb_build_object(
            'success', FALSE, 
            'strategy', 'below_threshold', 
            'matches', matches, 
            'geometry', NULL, 
            'name', NULL,
            'coordinates', NULL
        );
    END IF;
    
    match_count := jsonb_array_length(top_matches);
    
    -- Поиск пересечения для 2+ улиц (с учетом псевдопересечений)
    IF match_count >= 2 THEN
        street1_id := (top_matches->0->>'street_id')::INT;
        street2_id := (top_matches->1->>'street_id')::INT;
        
        -- Проверяем пересечение
        SELECT ST_PointOnSurface(ST_Intersection(a.geom, b.geom)) 
        INTO intersection_result
        FROM streets a, streets b 
        WHERE a.id = street1_id AND b.id = street2_id 
          AND ST_Intersects(a.geom, b.geom);
        
        -- Если нет пересечения, проверяем псевдопересечение
        IF intersection_result IS NULL OR ST_IsEmpty(intersection_result) THEN
            SELECT ST_Centroid(ST_Collect(
                ST_ClosestPoint(a.geom, b.geom),
                ST_ClosestPoint(b.geom, a.geom)
            ))
            INTO intersection_result
            FROM streets a, streets b
            WHERE a.id = street1_id AND b.id = street2_id
              AND ST_DWithin(
                  ST_Transform(a.geom, 3857),
                  ST_Transform(b.geom, 3857),
                  v_buffer_radius
              );
        END IF;
        
        IF intersection_result IS NOT NULL AND NOT ST_IsEmpty(intersection_result) THEN
            RETURN jsonb_build_object(
                'success', TRUE, 
                'strategy', 'intersection', 
                'matches', top_matches,
                'geometry', jsonb_build_object(
                    'type', 'Point', 
                    'coordinates', jsonb_build_array(
                        ST_X(intersection_result), 
                        ST_Y(intersection_result)
                    )
                ),
                'name', format('%s & %s', top_matches->0->>'name', top_matches->1->>'name'),
                'coordinates', jsonb_build_array(
                    ST_X(intersection_result), 
                    ST_Y(intersection_result)
                )
            );
        END IF;
    END IF;

    -- Одна улица или нет пересечения - берем ПОЛНУЮ геометрию первой улицы
    SELECT geom INTO v_street_geom
    FROM streets 
    WHERE id = (top_matches->0->>'street_id')::INT;

    -- Вычисляем центроид для coordinates (для удобства отображения)
    IF v_street_geom IS NOT NULL THEN
        v_centroid := ST_Centroid(v_street_geom);
    ELSE
        v_centroid := NULL;
    END IF;

    RETURN jsonb_build_object(
        'success', TRUE,
        'strategy', 'single_match',
        'matches', top_matches,
        'geometry', jsonb_build_object(
            'type', regexp_replace(INITCAP(GeometryType(v_street_geom)), '^St_', ''),
            'coordinates', ST_AsGeoJSON(v_street_geom)::json
        ),
        'name', top_matches->0->>'name',
        'coordinates', CASE 
            WHEN v_centroid IS NOT NULL 
            THEN jsonb_build_array(ST_X(v_centroid), ST_Y(v_centroid))
            ELSE NULL
        END
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION search_locations IS 'Поиск локаций по тексту с двухстадийным алгоритмом (pg_trgm, threshold 0.67)';

-- =============================================================================
-- Функция генерации случайной точки в круге
-- =============================================================================

CREATE OR REPLACE FUNCTION generate_random_location_v2(
    reason TEXT DEFAULT 'random',
    center_lon FLOAT DEFAULT 30.83135,
    center_lat FLOAT DEFAULT 46.49804,
    radius FLOAT DEFAULT 0.045
)
RETURNS JSONB AS $$
DECLARE
    point_geom JSONB;
    random_angle FLOAT;
    random_radius FLOAT;
    point_lon FLOAT;
    point_lat FLOAT;
BEGIN
    -- Генерация случайной точки в круге (равномерное распределение)
    -- Используем полярные координаты: случайный угол и случайный радиус с sqrt для равномерности
    random_angle := 2.0 * pi() * random();
    random_radius := radius * sqrt(random());
    
    point_lon := center_lon + random_radius * cos(random_angle);
    point_lat := center_lat + random_radius * sin(random_angle);
    
    point_geom := jsonb_build_object(
        'type', 'Point',
        'coordinates', jsonb_build_array(
            point_lon,
            point_lat
        )
    );
    RETURN jsonb_build_object(
        'name', 'Random Location',
        'geom', point_geom,
        'coordinates', point_geom->'coordinates',
        'matches', '[]'::JSONB,
        'strategy', reason
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_random_location_v2 IS 'Генерация случайной точки в круге (центр, радиус)';

-- =============================================================================
-- Функция и задача для автоматической очистки старых событий
-- =============================================================================

CREATE OR REPLACE FUNCTION clean_old_events()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM events 
    WHERE event_time < NOW() - INTERVAL '1 hour';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    IF deleted_count > 0 THEN
        UPDATE events_meta 
        SET version = version + 1,
            updated_at = NOW()
        WHERE id = 1;
        
        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_count,
            'cleaned_at', NOW()
        )::text);
    END IF;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Удаляем старую задачу если есть
SELECT cron.unschedule('clean-old-events') WHERE EXISTS (
    SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events'
);

-- Создаем задачу: каждые 5 минут
SELECT cron.schedule('clean-old-events', '*/5 * * * *', 'SELECT clean_old_events()');

COMMENT ON FUNCTION clean_old_events IS 'Удаляет события старше 1 часа, запускается cron каждые 5 минут';
