-- 11-partition-maintenance.sql
-- Partition management for hourly partitions (realtime, 60-min TTL)
-- Создаёт партиции на текущий час + 1 вперёд, удаляет старше 1 часа

CREATE OR REPLACE FUNCTION manage_event_partitions()
RETURNS INTEGER AS $$
DECLARE
    part_hour TIMESTAMPTZ;
    part_name TEXT;
    created_count INTEGER := 0;
    cutoff_hour TIMESTAMPTZ;
BEGIN
    -- Создаём текущий час + 1 час вперёд
    FOR i IN 0..1 LOOP
        part_hour := date_trunc('hour', NOW()) + i * INTERVAL '1 hour';
        part_name := 'events_' || to_char(part_hour, 'YYYY_MM_DD_HH24');
        IF NOT EXISTS (
            SELECT 1 FROM pg_class WHERE relname = part_name
        ) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
                part_name,
                part_hour,
                part_hour + INTERVAL '1 hour'
            );
            created_count := created_count + 1;
        END IF;
    END LOOP;

    -- Удаляем партиции, которые целиком старше 1 часа
    cutoff_hour := date_trunc('hour', NOW()) - INTERVAL '1 hour';
    FOR part_name IN
        SELECT relname FROM pg_class
        WHERE relname ~ '^events_\d{4}_\d{2}_\d{2}_\d{2}$'
          AND relkind = 'r'
          AND to_timestamp(substring(relname FROM 8 FOR 13), 'YYYY_MM_DD_HH24') < cutoff_hour
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I', part_name);
        created_count := created_count + 1;
    END LOOP;

    RETURN created_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание если есть
SELECT cron.unschedule('manage-event-partitions')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'manage-event-partitions');

-- Создаём задание каждые 5 минут (часовые партиции создаются редко, но проверять надо чаще)
SELECT cron.schedule('manage-event-partitions', '*/5 * * * *', 'SELECT manage_event_partitions()');
