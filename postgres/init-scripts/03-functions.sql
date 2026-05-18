-- 03-functions.sql
-- Периодическая очистка событий старше 1 часа (pg_cron каждые 5 минут).
--
-- Порядок работы:
--   1. До удаления строк собираем photo_url у тех событий, у которых он есть.
--   2. Удаляем устаревшие строки.
--   3. Если что-то удалено — увеличиваем версию events_meta и отправляем
--      NOTIFY events_cleaned с полем photo_urls. Сервис parser (у которого
--      /media/events монтируется :rw) слушает это уведомление и удаляет файлы.

CREATE OR REPLACE FUNCTION clean_old_events()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
    photo_urls    TEXT[];  -- пути к фото удаляемых событий (может быть NULL если нет ни одного)
BEGIN
    -- Собираем пути к фото до DELETE, чтобы передать их в NOTIFY
    SELECT array_agg(photo_url) INTO photo_urls
    FROM events
    WHERE event_time < NOW() - INTERVAL '1 hour'
      AND photo_url IS NOT NULL;

    DELETE FROM events
    WHERE event_time < NOW() - INTERVAL '1 hour';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    IF deleted_count > 0 THEN
        UPDATE events_meta
        SET version    = version + 1,
            updated_at = NOW()
        WHERE id = 1;

        -- photo_urls: COALESCE гарантирует пустой массив вместо JSON null,
        -- если ни одно из удалённых событий не имело фото.
        PERFORM pg_notify('events_cleaned', jsonb_build_object(
            'deleted_count', deleted_count,
            'cleaned_at',    NOW(),
            'photo_urls',    COALESCE(to_jsonb(photo_urls), '[]'::jsonb)
        )::text);
    END IF;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Снимаем старое задание (идемпотентно при первом запуске вернёт пустой SELECT)
SELECT cron.unschedule('clean-old-events')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'clean-old-events');

SELECT cron.schedule('clean-old-events', '*/5 * * * *', 'SELECT clean_old_events()');
