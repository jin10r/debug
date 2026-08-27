-- Migration: Refresh Token Rotation support
-- Обеспечивает single-use refresh tokens (RFC 6749 best practice).
-- Каждый refresh-токен может быть использован ровно один раз.
-- Повторное использование = признак кражи → инвалидация всех токенов пользователя.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,               -- NULL = не использован
    revoked     BOOLEAN NOT NULL DEFAULT FALSE
);

-- Быстрый поиск активных токенов конкретного пользователя
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_active
    ON refresh_tokens(user_id)
    WHERE used_at IS NULL AND revoked = FALSE;

-- Быстрая очистка истёкших токенов
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires
    ON refresh_tokens(expires_at);

-- Автоматическая очистка истёкших токенов (запускается при каждом подключении к БД).
-- Удаляет записи старше суток после истечения, чтобы таблица не разрасталась.
CREATE OR REPLACE FUNCTION cleanup_expired_refresh_tokens() RETURNS void
    LANGUAGE plpgsql AS
$$
BEGIN
    DELETE FROM refresh_tokens
    WHERE expires_at < now() - INTERVAL '1 day';
END;
$$;

COMMENT ON TABLE refresh_tokens IS
    'Single-use refresh tokens for JWT rotation. Повторное использование jti = кража токена.';

-- Плановая очистка истёкших refresh-токенов (запуск каждые 6 часов)
SELECT cron.unschedule('cleanup-refresh-tokens')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'cleanup-refresh-tokens');

SELECT cron.schedule(
    'cleanup-refresh-tokens',
    '0 */6 * * *',
    'SELECT cleanup_expired_refresh_tokens()'
);
