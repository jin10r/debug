-- 14-training-examples.sql
-- Накопление реальных примеров (input → target) для обучения GeoIntentSeq2Seq.
-- Заполняется рантайм-коллектором в message_processor.py.
-- Уникальность по (message_id, event_time) — идемпотентность при ретраях.

CREATE TABLE IF NOT EXISTS training_examples (
    id SERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    input_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (message_id, event_time)
);
