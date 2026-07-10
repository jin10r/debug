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

-- Временная таблица для geo (WKT данные с pipe-разделителем синонимов)
CREATE TEMP TABLE IF NOT EXISTS temp_geo_wkt (names TEXT, wkt_geom TEXT, type TEXT);

-- Загружаем CSV во временную таблицу
COPY temp_geo_wkt(names, wkt_geom, type)
FROM '/docker-entrypoint-initdb.d/data/geo.csv'
WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

-- Безопасный парсер WKT: одна битая геометрия не должна валить ВЕСЬ INSERT
-- (а с ним и инициализацию БД — postgres exit 3 → geo пустой → все события
-- становятся 'random'). Невалидная строка пропускается с WARNING.
CREATE OR REPLACE FUNCTION safe_geom_from_text(wkt text, srid int)
RETURNS geometry AS $$
BEGIN
    RETURN ST_SetSRID(ST_GeomFromText(wkt, srid), srid);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'geo load: skipping invalid geometry: %', left(wkt, 80);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Вставляем geo с преобразованием pipe-разделителя в массив.
-- safe_geom_from_text вычисляется один раз в подзапросе; строки с NULL-геометрией
-- (битый WKT) отсекаются — остальные грузятся.
INSERT INTO geo(names, type, geom)
SELECT names_arr, type, geom
FROM (
    SELECT
        string_to_array(names, '|') AS names_arr,  -- разбиваем по pipe на массив
        safe_geom_from_text(wkt_geom, 4326)         AS geom,
        type
    FROM temp_geo_wkt
) s
WHERE geom IS NOT NULL
ON CONFLICT DO NOTHING;

DROP TABLE temp_geo_wkt;

-- Загрузка layer_keywords (через ON CONFLICT)
-- Синхронизировано с parser/layer_classifier.py DEFAULT_LAYER_KEYWORDS
-- pig — fallback без ключей (слой определяется, если ни bus/cops/traffic не совпал)
INSERT INTO layer_keywords (layer, keywords) VALUES
    ('bus', ARRAY['автобус', 'бус', 'хайс', 'спринтер', 'рено', 'фольксваген', 'фольц', 'хёндай', 'вито', 'сталкер', 'транспортёр', 'h1', 'h2', 'h3', 'h4', 'h5', 'т1', 'т2', 'т3', 'т4', 'т5', 'н1', 'н2', 'н3', 'н4', 'н5', 'буса', 'бусик']),
    ('cops', ARRAY['коп', 'полиция', 'мусор', 'мусара', 'люстра', 'мигалка', 'патруль', 'экипаж', 'мент', 'менты', 'менти', 'полицейский', 'полицай', 'police', 'мусорня', 'мусорской', 'сирена']),
    ('traffic', ARRAY['дтп', 'авария', 'пробка', 'затор', 'светофор', 'блокпост', 'пост', 'бп', 'б/п']),
    ('pig', ARRAY[]::text[])
ON CONFLICT (layer) DO UPDATE SET keywords = EXCLUDED.keywords;

ANALYZE geo;
