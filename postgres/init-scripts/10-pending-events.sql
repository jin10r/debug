-- 10-pending-events.sql
-- Очередь сообщений для processor: парсер пишет, процессор читает (SKIP LOCKED).

CREATE TABLE IF NOT EXISTS pending_events (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    text TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    photo_file_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'error')),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    processed_at TIMESTAMPTZ,
    UNIQUE(message_id, event_time)
);

CREATE INDEX IF NOT EXISTS idx_pending_events_status
    ON pending_events(status, created_at)
    WHERE status = 'pending';

-- NOTIFY parser о необходимости скачать фото после обработки events.
CREATE OR REPLACE FUNCTION notify_photo_download()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'done' AND OLD.status IS DISTINCT FROM 'done'
       AND NEW.photo_file_id IS NOT NULL THEN
        PERFORM pg_notify('photo_download',
            jsonb_build_object(
                'event_id', NEW.id,
                'message_id', NEW.message_id,
                'photo_file_id', NEW.photo_file_id
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pending_events_photo_notify
    AFTER UPDATE ON pending_events
    FOR EACH ROW
    EXECUTE FUNCTION notify_photo_download();
