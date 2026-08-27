-- 03-functions.sql
-- Периодическая очистка событий старше 1 часа (pg_cron каждые 5 минут).
-- Partition-aware: если целая партиция попадает в TTL-окно, используется
-- DROP TABLE вместо DELETE для мгновенной очистки.
--
-- Расписание `3-59/5` (минуты 3, 8, 13, ...) — НЕ совпадает с расписанием
-- manage-event-partitions (`*/5`, минуты 0, 5, 10, ...). Раньше обе задачи
-- стартовали одновременно в минуты 0/5/10: clean_old_events брал
-- AccessShareLock на events, а manage_event_partitions держал
-- AccessExclusiveLock (CREATE TABLE ... PARTITION OF events) — чистка
-- ждала блокировку ~1с каждые 5 минут.

CREATE OR REPLACE FUNCTION clean_old_events()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER := 0;
    partition_name TEXT;
    cutoff_time TIMESTAMPTZ := NOW() - INTERVAL '60 minutes';
BEGIN
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

    IF deleted_count > 0 THEN
        UPDATE events_meta
        SET version = version + 1,
            updated_at = NOW()
        WHERE id = 1;

        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_count,
            'cleaned_at', NOW(),
            'photo_urls', '[]'::jsonb
        )::text);
    END IF;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание
SELECT cron.unschedule('clean-old-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events');

SELECT cron.schedule('clean-old-events', '3-59/5 * * * *', 'SELECT clean_old_events()');
