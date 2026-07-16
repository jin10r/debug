-- Migration: 012_geo_notify_trigger.sql
-- Триггер для уведомления парсера об изменении geo

-- =============================================================================
-- Функция уведомления
-- =============================================================================

CREATE OR REPLACE FUNCTION notify_geo_updated()
RETURNS TRIGGER AS $$
DECLARE
    v_geo_name TEXT;
    v_geo_id INT;
BEGIN
    -- Определяем имя и ID объекта
    IF TG_OP = 'DELETE' THEN
        v_geo_name := OLD.names[1];
        v_geo_id := OLD.id;
    ELSE
        v_geo_name := NEW.names[1];
        v_geo_id := NEW.id;
    END IF;

    -- Отправляем уведомление парсеру с geo_id для targeted reindex
    PERFORM pg_notify('geo_updated', jsonb_build_object(
        'operation', TG_OP,
        'table', 'geo',
        'geo_id', v_geo_id,
        'timestamp', NOW(),
        'message', 'Geo table changed, please refresh cache'
    )::text);

    -- Логгируем изменение (опционально)
    IF TG_OP = 'DELETE' THEN
        RAISE NOTICE 'Geo deleted: % (%)', v_geo_name, v_geo_id;
    ELSIF TG_OP = 'INSERT' THEN
        RAISE NOTICE 'Geo added: % (%)', v_geo_name, v_geo_id;
    ELSIF TG_OP = 'UPDATE' THEN
        RAISE NOTICE 'Geo updated: % (%)', v_geo_name, v_geo_id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION notify_geo_updated IS
    'Уведомляет парсер об изменении таблицы geo через pg_notify';

-- =============================================================================
-- Триггер
-- =============================================================================

-- Удаляем старый триггер если есть
DROP TRIGGER IF EXISTS geo_updated_trigger ON geo;

-- Создаём триггер на INSERT/UPDATE/DELETE
CREATE TRIGGER geo_updated_trigger
    AFTER INSERT OR UPDATE OR DELETE ON geo
    FOR EACH STATEMENT
    EXECUTE FUNCTION notify_geo_updated();

COMMENT ON TRIGGER geo_updated_trigger ON geo IS
    'Срабатывает при изменении geo и уведомляет парсер';

-- =============================================================================
-- Проверка:
--
-- -- Вручную отправить уведомление для теста
-- SELECT pg_notify('geo_updated', '{"test": true}');
--
-- -- Проверить подписку в парсере
-- SELECT * FROM pg_listening_channels();
-- =============================================================================
