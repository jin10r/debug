-- 11-partition-maintenance.sql
-- Partition management for hourly partitions.
--
-- Two-tier cleanup strategy (Option C):
--   1. clean_old_events() — primary TTL enforcement, drops partitions > 60 min old.
--      Runs every 5 min via pg_cron, owns the ephemeral-buffer lifecycle.
--   2. manage_event_partitions() — safety net for partitions older than 3 hours.
--      Dead code under healthy cluster (clean_old_events reaps at 60 min),
--      but catches runaway growth if clean_old_events stalls. When it does
--      drop partitions it fires pg_notify('partition_overflow') for alerting.
--
-- Creates partitions from -1h to +1h (3 partitions).

CREATE OR REPLACE FUNCTION manage_event_partitions()
RETURNS INTEGER AS $$
DECLARE
    part_hour TIMESTAMPTZ;
    part_name TEXT;
    created_count INTEGER := 0;
    dropped_count INTEGER := 0;
    cutoff_hour TIMESTAMPTZ;
BEGIN
    FOR i IN -1..1 LOOP
        part_hour := date_trunc('hour', NOW()) + i * INTERVAL '1 hour';
        part_name := 'events_' || to_char(part_hour, 'YYYY_MM_DD_HH24');
        IF to_regclass(part_name) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
                part_name,
                part_hour,
                part_hour + INTERVAL '1 hour'
            );
            EXECUTE format('ANALYZE %I', part_name);
            created_count := created_count + 1;
        END IF;
    END LOOP;

    cutoff_hour := date_trunc('hour', NOW()) - INTERVAL '3 hours';
    FOR part_name IN
        SELECT relname FROM pg_class
        WHERE relname ~ '^events_\d{4}_\d{2}_\d{2}_\d{2}$'
          AND relkind = 'r'
          AND to_timestamp(substring(relname FROM 8 FOR 13), 'YYYY_MM_DD_HH24') < cutoff_hour
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I', part_name);
        dropped_count := dropped_count + 1;
    END LOOP;

    IF dropped_count > 0 THEN
        PERFORM pg_notify(
            'partition_overflow',
            json_build_object(
                'dropped_count', dropped_count,
                'ran_at', NOW()
            )::TEXT
        );
        UPDATE events_meta
        SET version = version + 1,
            updated_at = NOW()
        WHERE id = 1;
    END IF;

    RETURN created_count;
END;
$$ LANGUAGE plpgsql;

SELECT cron.unschedule('manage-event-partitions')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'manage-event-partitions');

SELECT cron.schedule('manage-event-partitions', '*/5 * * * *', 'SELECT manage_event_partitions()');
