-- Migration: 012_streets_notify_trigger.sql
-- Триггер для уведомления парсера об изменении улиц

-- =============================================================================
-- Функция уведомления
-- =============================================================================

CREATE OR REPLACE FUNCTION notify_streets_updated()
RETURNS TRIGGER AS $$
BEGIN
    -- Отправляем уведомление парсеру
    PERFORM pg_notify('streets_updated', jsonb_build_object(
        'operation', TG_OP,
        'table', 'streets',
        'timestamp', NOW(),
        'message', 'Streets table changed, please refresh cache'
    )::text);

    -- Логгируем изменение (опционально)
    IF TG_OP = 'DELETE' THEN
        RAISE NOTICE 'Street deleted: % (%)', OLD.name, OLD.id;
    ELSIF TG_OP = 'INSERT' THEN
        RAISE NOTICE 'Street added: % (%)', NEW.name, NEW.id;
    ELSIF TG_OP = 'UPDATE' THEN
        RAISE NOTICE 'Street updated: % (%)', NEW.name, NEW.id;
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
