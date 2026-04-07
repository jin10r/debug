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

-- Вставляем улицы с преобразованием pipe-разделителя в массив
INSERT INTO streets(names, geom)
SELECT
    string_to_array(names, '|'),  -- разбиваем по pipe на массив
    ST_SetSRID(ST_GeomFromText(wkt_geom, 4326), 4326)
FROM temp_streets_wkt
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
