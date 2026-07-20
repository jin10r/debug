from aiogram import BaseMiddleware
from typing import Dict, Any, Callable, Awaitable
from core.db.dbconnect import Request

class DbMiddleware(BaseMiddleware):
    """Middleware to inject database request handler."""
    
    def __init__(self, request: Request):
        self.request = request

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:
        data["request"] = self.request
        return await handler(event, data)