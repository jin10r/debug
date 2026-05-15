-- Migration: 012_streets_notify_trigger.sql
-- Триггер для уведомления парсера об изменении улиц

-- =============================================================================
-- Функция уведомления
-- =============================================================================

CREATE OR REPLACE FUNCTION notify_streets_updated()
RETURNS TRIGGER AS $$
DECLARE
    v_street_name TEXT;
    v_street_id INT;
BEGIN
    -- Определяем имя и ID улицы
    IF TG_OP = 'DELETE' THEN
        v_street_name := OLD.names[1];
        v_street_id := OLD.id;
    ELSE
        v_street_name := NEW.names[1];
        v_street_id := NEW.id;
    END IF;

    -- Отправляем уведомление парсеру
    PERFORM pg_notify('streets_updated', jsonb_build_object(
        'operation', TG_OP,
        'table', 'streets',
        'timestamp', NOW(),
        'message', 'Streets table changed, please refresh cache'
    )::text);

    -- Логгируем изменение (опционально)
    IF TG_OP = 'DELETE' THEN
        RAISE NOTICE 'Street deleted: % (%)', v_street_name, v_street_id;
    ELSIF TG_OP = 'INSERT' THEN
        RAISE NOTICE 'Street added: % (%)', v_street_name, v_street_id;
    ELSIF TG_OP = 'UPDATE' THEN
        RAISE NOTICE 'Street updated: % (%)', v_street_name, v_street_id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION notify_streets_updated IS
    'Уведомляет парсер об изменении таблицы streets через pg_notify';

-- =============================================================================
-- Триггер
-- =============================================================================

-- Удаляем старый триггер если есть
DROP TRIGGER IF EXISTS streets_updated_trigger ON streets;

-- Создаём триггер на INSERT/UPDATE/DELETE
CREATE TRIGGER streets_updated_trigger
    AFTER INSERT OR UPDATE OR DELETE ON streets
    FOR EACH STATEMENT
    EXECUTE FUNCTION notify_streets_updated();

COMMENT ON TRIGGER streets_updated_trigger ON streets IS
    'Срабатывает при изменении streets и уведомляет парсер';

-- =============================================================================
-- Проверка:
--
-- -- Вручную отправить уведомление для теста
-- SELECT pg_notify('streets_updated', '{"test": true}');
--
-- -- Проверить подписку в парсере
-- SELECT * FROM pg_listening_channels();
-- =============================================================================
