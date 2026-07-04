-- 11-partition-maintenance.sql
-- Phase 3.1: Automatic partition management
-- Создаёт партиции на N дней вперёд, удаляет старые

CREATE OR REPLACE FUNCTION manage_event_partitions()
RETURNS INTEGER AS $$
DECLARE
    start_date DATE;
    end_date DATE;
    part_date DATE;
    part_name TEXT;
    created_count INTEGER := 0;
BEGIN
    -- Партиции на 3 дня вперёд (сегодня + 2)
    start_date := date_trunc('day', NOW())::DATE;
    end_date := start_date + 2;
    part_date := start_date;

    WHILE part_date <= end_date LOOP
        part_name := 'events_' || to_char(part_date, 'YYYY_MM_DD');
        IF NOT EXISTS (
            SELECT 1 FROM pg_class WHERE relname = part_name
        ) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
                part_name,
                part_date,
                part_date + 1
            );
            created_count := created_count + 1;
        END IF;
        part_date := part_date + 1;
    END LOOP;

    -- Удаляем партиции старше 3 дней (данные TTL 1 час, но оставляем запас)
    FOR part_name IN
        SELECT relname FROM pg_class
        WHERE relname ~ '^events_\d{4}_\d{2}_\d{2}$'
          AND relkind = 'r'
          AND substring(relname FROM 8 FOR 10)::DATE < start_date - 3
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I', part_name);
    END LOOP;

    RETURN created_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание если есть
SELECT cron.unschedule('manage-event-partitions')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'manage-event-partitions');

-- Создаём задание на каждый час
SELECT cron.schedule('manage-event-partitions', '0 * * * *', 'SELECT manage_event_partitions()');
