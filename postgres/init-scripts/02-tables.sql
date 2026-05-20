-- 02-tables.sql
-- Минимальная схема без избыточности

-- Справочник улиц с синонимами (names TEXT[])
CREATE TABLE IF NOT EXISTS streets (
    id SERIAL PRIMARY KEY,
    names TEXT[] NOT NULL,        -- массив синонимов названий
    geom GEOMETRY(Geometry, 4326) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_streets_names ON streets USING gin (names);
CREATE INDEX IF NOT EXISTS idx_streets_geom ON streets USING gist (geom);

-- Стоп-слова
CREATE TABLE IF NOT EXISTS stopwords (word TEXT PRIMARY KEY);

-- Ключевые слова для определения слоя
CREATE TABLE IF NOT EXISTS layer_keywords (
    layer TEXT PRIMARY KEY,
    keywords TEXT[] NOT NULL
);

-- Основная таблица событий (единственная, без raw_data)
-- Инварианты:
--   layer — закрытое множество слоёв (см. parser/layer_classifier.py);
--   description — ограничение 500 символов (parser limit-ит до 380 через
--     MAX_TEXT_LENGTH, БД страхует на случай bypass).
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    message_id BIGINT,                 -- Telegram message id (дедупликация)
    event_time TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL CHECK (char_length(description) <= 500),
    photo_url TEXT,
    layer TEXT NOT NULL DEFAULT 'pig'
        CHECK (layer IN ('pig', 'cops', 'bus', 'traffic')),
    matches JSONB,
    strategy VARCHAR(40) NOT NULL CHECK (strategy IN (
        'random',
        'single_match',
        'single_intersection',
        'polygon_intersection'
    )),
    geom GEOMETRY
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_geom ON events USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_events_layer ON events(layer);

-- message_id + уникальный индекс делают вставку события идемпотентной
-- (INSERT ... ON CONFLICT (message_id) DO NOTHING): бэкфилл истории канала и
-- ретраи воркера не создают дублей. ALTER ... IF NOT EXISTS — совместимость с
-- уже существующей таблицей. NULL в message_id допустимы (несколько NULL не
-- конфликтуют) — legacy-строки не ломаются.
ALTER TABLE events ADD COLUMN IF NOT EXISTS message_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_message_id ON events(message_id);

-- CHECK strategy сужен до 4 значений, которые реально выдают process_candidates
-- и parser; для уже существующей таблицы пересоздаём ограничение идемпотентно.
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_strategy_check;
ALTER TABLE events ADD CONSTRAINT events_strategy_check
    CHECK (strategy IN ('random', 'single_match', 'single_intersection', 'polygon_intersection'));

-- Идемпотентные ALTER-блоки для уже существующих таблиц (events.layer
-- допустимые значения и events.description длина). На новой БД ограничения
-- уже стоят в CREATE TABLE — DROP+ADD пересоздаёт их под тем же именем.
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_layer_check;
ALTER TABLE events ADD CONSTRAINT events_layer_check
    CHECK (layer IN ('pig', 'cops', 'bus', 'traffic'));

ALTER TABLE events DROP CONSTRAINT IF EXISTS events_description_length;
ALTER TABLE events ADD CONSTRAINT events_description_length
    CHECK (char_length(description) <= 500);

-- Метаданные для синхронизации WebSocket
CREATE TABLE IF NOT EXISTS events_meta (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now(),
    max_event_id INT DEFAULT 0
);

INSERT INTO events_meta (id, version, updated_at, max_event_id)
VALUES (1, 0, now(), 0)
ON CONFLICT (id) DO NOTHING;
