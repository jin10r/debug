-- 03-functions.sql
-- Периодическая очистка событий старше 1 часа (pg_cron каждые 5 минут).
-- Partition-aware: если целая партиция попадает в TTL-окно, используется
-- DROP TABLE вместо DELETE для мгновенной очистки.

CREATE OR REPLACE FUNCTION clean_old_events()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER := 0;
    photo_urls    TEXT[];
    partition_name TEXT;
    cutoff_time TIMESTAMPTZ := NOW() - INTERVAL '1 hour';
BEGIN
    -- Шаг 1: DROP партиций, целиком попавших в TTL
    FOR partition_name IN
        SELECT c.relname AS partition_name
        FROM pg_class c
        JOIN pg_inherits i ON c.oid = i.inhrelid
        JOIN pg_class p ON i.inhparent = p.oid
        WHERE p.relname = 'events'
          AND c.relname ~ '^events_\d{4}_\d{2}_\d{2}_\d{2}$'
          AND c.relkind = 'r'
          AND to_timestamp(
              substring(c.relname FROM 8 FOR 13),
              'YYYY_MM_DD_HH24'
          ) + INTERVAL '1 hour' < cutoff_time
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I', partition_name);
        deleted_count := deleted_count + 1;
    END LOOP;

    -- Шаг 2: Собираем photo_url до DELETE
    SELECT array_agg(photo_url) INTO photo_urls
    FROM events
    WHERE event_time < cutoff_time
      AND photo_url IS NOT NULL;

    -- Шаг 3: DELETE из текущей (неполной) партиции
    DELETE FROM events
    WHERE event_time < cutoff_time;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    IF deleted_count > 0 OR photo_urls IS NOT NULL THEN
        UPDATE events_meta
        SET version    = version + 1,
            updated_at = NOW()
        WHERE id = 1;

        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_count,
            'cleaned_at',    NOW(),
            'photo_urls',    COALESCE(to_jsonb(photo_urls), '[]'::jsonb)
        )::text);
    END IF;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание
SELECT cron.unschedule('clean-old-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events');

SELECT cron.schedule('clean-old-events', '*/5 * * * *', 'SELECT clean_old_events()');
