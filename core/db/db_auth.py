"""
Auth-related database operations: refresh token storage and rotation.

Обеспечивает Refresh Token Rotation (single-use):
- store_refresh_token  — сохранить новый refresh-токен при выдаче
- consume_refresh_token — атомарно пометить использованным (возвращает False если уже использован)
- revoke_all_user_tokens — инвалидировать все токены пользователя (реакция на кражу)
- cleanup_expired_tokens — удалить истёкшие записи (периодическая задача)
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class AuthOperations:
    """Операции с refresh-токенами в БД."""

    def __init__(self, db):
        self.db = db

    async def store_refresh_token(
        self,
        jti: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Сохранить новый refresh-токен.

        Args:
            jti: Уникальный идентификатор токена (JWT ID claim).
            user_id: Telegram user ID (строка).
            expires_at: Время истечения (timezone-aware).
        """
        await self.db.execute(
            """
            INSERT INTO refresh_tokens (jti, user_id, expires_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (jti) DO NOTHING
            """,
            jti, user_id, expires_at,
        )

    async def consume_refresh_token(self, jti: str) -> bool:
        """Атомарно пометить refresh-токен использованным.

        Returns:
            True  — токен был активен и успешно помечен использованным.
            False — токен не найден, уже использован, отозван или истёк
                    (любой из этих случаев означает отказ).

        Атомарность UPDATE гарантирует отсутствие race condition при
        параллельных запросах с одним jti.
        """
        result = await self.db.fetchval(
            """
            UPDATE refresh_tokens
            SET used_at = now()
            WHERE jti = $1
              AND used_at IS NULL
              AND revoked = FALSE
              AND expires_at > now()
            RETURNING jti
            """,
            jti,
        )
        return result is not None

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """Инвалидировать все активные refresh-токены пользователя.

        Вызывается при обнаружении повторного использования токена
        (признак компрометации). Возвращает количество отозванных токенов.
        """
        result = await self.db.execute(
            """
            UPDATE refresh_tokens
            SET revoked = TRUE
            WHERE user_id = $1
              AND revoked = FALSE
            """,
            user_id,
        )
        # asyncpg возвращает строку вида "UPDATE N"
        try:
            count = int(result.split()[-1])
        except (AttributeError, ValueError, IndexError):
            count = 0
        if count > 0:
            logger.warning(
                f"Revoked {count} refresh token(s) for user {user_id} "
                "(token reuse detected — possible theft)"
            )
        return count

    async def cleanup_expired_tokens(self) -> int:
        """Удалить истёкшие и старые использованные токены.

        Безопасно вызывать периодически (например, раз в час).
        Возвращает количество удалённых записей.
        """
        result = await self.db.execute(
            "SELECT cleanup_expired_refresh_tokens()"
        )
        return 0  # функция void, результат несущественен
