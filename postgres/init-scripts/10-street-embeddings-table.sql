-- 10-street-embeddings-table.sql
-- Отдельная таблица для embeddings каждого названия улицы.
-- Позволяет находить улицу по любому из синонимов (например "старопортофранковская" 
-- для улицы "комсомольская|Старопортофранковская").

CREATE EXTENSION IF NOT EXISTS vector;

-- Таблица: один embedding на одно название улицы
CREATE TABLE IF NOT EXISTS street_embeddings (
    street_id INTEGER NOT NULL REFERENCES streets(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    embedding vector(312),
    PRIMARY KEY (street_id, name)
);

-- HNSW индекс для быстрого поиска по косинусному сходству
CREATE INDEX IF NOT EXISTS idx_street_embeddings_embedding
    ON street_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (M = 16, ef_construction = 64);

COMMENT ON TABLE street_embeddings IS 'Embeddings для каждого названия улицы (синонимы)';
COMMENT ON COLUMN street_embeddings.embedding IS 'ONNX rubert-tiny2 embedding для одного названия улицы';

-- Миграция: скопировать существующие embeddings из streets.street_embeddings
-- (если они были созданы ранее через 09-semantic-embeddings.sql)
-- NOTE: Миграция отключена - столбец embedding не существует в таблице streets
-- INSERT INTO street_embeddings (street_id, name, embedding)
-- SELECT s.id, LOWER(TRIM(s.names[1])), s.embedding
-- FROM streets s
-- WHERE s.embedding IS NOT NULL
--   AND s.names IS NOT NULL
--   AND array_length(s.names, 1) > 0
--   AND NOT EXISTS (
--       SELECT 1 FROM street_embeddings se WHERE se.street_id = s.id
--   )
-- ON CONFLICT (street_id, name) DO NOTHING;
