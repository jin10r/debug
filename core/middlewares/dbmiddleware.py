from aiogram import BaseMiddleware
from typing import Dict, Any, Callable, Awaitable
from core.db.db_base import Database

class DbMiddleware(BaseMiddleware):
    """Middleware to inject database pool into aiogram handler data."""
    
    def __init__(self, db_pool: Database):
        self.db_pool = db_pool

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:
        data["request"] = self.db_pool
        return await handler(event, data)