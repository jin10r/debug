-- 04-load-data.sql
-- Загрузка начальных данных (идемпотентная - можно запускать многократно)
-- Данные в WKT формате для прямой загрузки

-- Временная таблица для загрузки stopwords
CREATE TEMP TABLE IF NOT EXISTS temp_stopwords (word TEXT);

-- Загружаем CSV во временную таблицу
COPY temp_stopwords(word)
FROM '/docker-entrypoint-initdb.d/data/stopwords.csv'
WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

-- Вставляем только новые слова (игнорируем дубликаты)
INSERT INTO stopwords(word)
SELECT word FROM temp_stopwords
ON CONFLICT (word) DO NOTHING;

DROP TABLE temp_stopwords;

-- Временная таблица для streets (WKT данные с pipe-разделителем синонимов)
CREATE TEMP TABLE IF NOT EXISTS temp_streets_wkt (names TEXT, wkt_geom TEXT);

-- Загружаем CSV во временную таблицу
COPY temp_streets_wkt(names, wkt_geom)
FROM '/docker-entrypoint-initdb.d/data/streets.csv'
WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

-- Безопасный парсер WKT: одна битая геометрия не должна валить ВЕСЬ INSERT
-- (а с ним и инициализацию БД — postgres exit 3 → streets пустой → все события
-- становятся 'random'). Невалидная строка пропускается с WARNING.
CREATE OR REPLACE FUNCTION safe_geom_from_text(wkt text, srid int)
RETURNS geometry AS $$
BEGIN
    RETURN ST_SetSRID(ST_GeomFromText(wkt, srid), srid);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'streets load: skipping invalid geometry: %', left(wkt, 80);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Вставляем улицы с преобразованием pipe-разделителя в массив.
-- safe_geom_from_text вычисляется один раз в подзапросе; строки с NULL-геометрией
-- (битый WKT) отсекаются — остальные грузятся.
INSERT INTO streets(names, geom)
SELECT names_arr, geom
FROM (
    SELECT
        string_to_array(names, '|') AS names_arr,  -- разбиваем по pipe на массив
        safe_geom_from_text(wkt_geom, 4326)         AS geom
    FROM temp_streets_wkt
) s
WHERE geom IS NOT NULL
ON CONFLICT DO NOTHING;

DROP TABLE temp_streets_wkt;

-- Загрузка layer_keywords (через ON CONFLICT)
INSERT INTO layer_keywords (layer, keywords) VALUES
    ('bus', ARRAY['автобус', 'троллейбус', 'трамвай', 'маршрутка', 'остановка', 'спринтер', 'рено', 'h1', 'h2', 'h3', 'h4', 'h5', 'фольц', 'хендай', 'Вито', 'бус']),
    ('cops', ARRAY['полиция', 'копы', 'коп', 'мусор', 'люстра', 'бп', 'блокпост', 'мигалки', 'патруль', 'пост', 'гаи', 'дпс']),
    ('traffic', ARRAY['дтп', 'авария', 'пробка', 'затор', 'закрыт', 'перекрыт', 'ремонт', 'реконструкция', 'стоянка', 'парковка', 'эвакуатор', 'сбил', 'наезд', 'столкновение', 'встречка', 'обочина']),
    ('pig', ARRAY['кабан', 'свинья', 'поросенок'])
ON CONFLICT (layer) DO UPDATE SET keywords = EXCLUDED.keywords;

ANALYZE streets;
