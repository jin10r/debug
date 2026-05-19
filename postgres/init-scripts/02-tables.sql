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
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    message_id BIGINT,                 -- Telegram message id (дедупликация)
    event_time TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL,
    photo_url TEXT,
    layer TEXT NOT NULL DEFAULT 'pig',
    matches JSONB,
    strategy VARCHAR(40) NOT NULL CHECK (strategy IN (
        'random',
        'single_match',
        'intersection',
        'polygon_intersection',
        'single_intersection',
        'full_intersection_geometry',
        'combined_geometries'
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
