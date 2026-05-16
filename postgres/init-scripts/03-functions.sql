-- 03-functions.sql
-- Периодическая очистка событий старше 1 часа (pg_cron каждые 5 минут).

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
        SET version    = version + 1,
            updated_at = NOW()
        WHERE id = 1;

        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_count,
            'cleaned_at',    NOW()
        )::text);
    END IF;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание (идемпотентно при первом запуске вернёт пустой SELECT)
SELECT cron.unschedule('clean-old-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events');

SELECT cron.schedule('clean-old-events', '*/5 * * * *', 'SELECT clean_old_events()');
