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
    dropped_partitions INTEGER := 0;
    deleted_rows INTEGER := 0;
    partition_name TEXT;
    cutoff TIMESTAMPTZ := NOW() - INTERVAL '60 minutes';
    photo_urls JSONB := '[]'::jsonb;
    partition_photos JSONB;
    deleted_photos JSONB;
BEGIN
    FOR partition_name IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_inherits i ON c.oid = i.inhrelid
        JOIN pg_class p ON i.inhparent = p.oid
        WHERE p.relname = 'events'
          AND c.relname ~ '^events_\d{4}_\d{2}_\d{2}_\d{2}$'
          AND c.relkind = 'r'
          AND to_timestamp(substring(c.relname FROM 8 FOR 13), 'YYYY_MM_DD_HH24')
              + INTERVAL '1 hour' <= cutoff
    LOOP
        EXECUTE format(
            'SELECT coalesce(jsonb_agg(photo_url), ''[]''::jsonb) FROM %I WHERE photo_url IS NOT NULL',
            partition_name
        ) INTO partition_photos;

        photo_urls := photo_urls || coalesce(partition_photos, '[]'::jsonb);

        EXECUTE format('DROP TABLE IF EXISTS %I', partition_name);
        dropped_partitions := dropped_partitions + 1;
    END LOOP;

    WITH deleted AS (
        DELETE FROM events WHERE event_time < cutoff
        RETURNING photo_url
    )
    SELECT count(*) AS cnt,
           coalesce(jsonb_agg(photo_url) FILTER (WHERE photo_url IS NOT NULL), '[]'::jsonb) AS photos
    INTO   deleted_rows, deleted_photos
    FROM   deleted;
    photo_urls := photo_urls || coalesce(deleted_photos, '[]'::jsonb);

    IF dropped_partitions > 0 OR deleted_rows > 0 THEN
        UPDATE events_meta
        SET version = version + 1, updated_at = NOW()
        WHERE id = 1;

        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_rows,
            'dropped_partitions', dropped_partitions,
            'cleaned_at', NOW(),
            'photo_urls', photo_urls
        )::text);
    END IF;

    RETURN dropped_partitions + deleted_rows;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION clean_old_pending_events()
RETURNS INTEGER AS $$
DECLARE
    expired_count INTEGER;
    deleted_count INTEGER;
BEGIN
    UPDATE pending_events
    SET status = 'expired', processed_at = now(), locked_at = NULL, worker_id = NULL
    WHERE (event_time < NOW() - INTERVAL '60 minutes'
           OR event_time > NOW() + INTERVAL '5 minutes')
      AND status = 'pending';

    GET DIAGNOSTICS expired_count = ROW_COUNT;

    DELETE FROM pending_events
    WHERE (event_time < NOW() - INTERVAL '60 minutes'
           OR event_time > NOW() + INTERVAL '5 minutes')
      AND status IN ('done', 'error', 'expired');

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RETURN expired_count + deleted_count;
END;
$$ LANGUAGE plpgsql;

SELECT cron.unschedule('clean-old-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events');

SELECT cron.schedule('clean-old-events', '3-59/5 * * * *', 'SELECT clean_old_events()');

SELECT cron.unschedule('clean-old-pending-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-pending-events');

SELECT cron.schedule('clean-old-pending-events', '*/15 * * * *', 'SELECT clean_old_pending_events()');
