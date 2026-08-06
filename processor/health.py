"""HTTP healthcheck сервер для processor."""

import logging
from datetime import datetime, timezone

from aiohttp import web

from processor.metrics import metrics_handler

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(self):
        """Инициализация health-сервера."""
        self._last_heartbeat = datetime.now(timezone.utc)
        self._initialized = False

    def touch(self):
        """Обновление метки времени последней активности."""
        self._last_heartbeat = datetime.now(timezone.utc)

    def set_initialized(self, initialized: bool):
        """Установка флага готовности процессора."""
        self._initialized = initialized

    async def handle_live(self, request):
        """Liveness-проверка: сервер жив."""
        return web.Response(text="OK")

    async def handle_ready(self, request):
        """Readiness-проверка: процессор инициализирован и активен."""
        if not self._initialized:
            return web.Response(status=503, text="Not initialized")

        age = (datetime.now(timezone.utc) - self._last_heartbeat).total_seconds()
        if age > 60:
            return web.Response(status=503, text=f"Stale heartbeat: {age:.1f}s")

        return web.Response(text="OK")

    async def start(self, port: int = 8765):
        """Запуск HTTP-сервера healthcheck."""
        app = web.Application()
        app.router.add_get("/health/live", self.handle_live)
        app.router.add_get("/health/ready", self.handle_ready)
        app.router.add_get("/metrics", metrics_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104 — bind all interfaces (health endpoint)
        await site.start()

        logger.info(f"Health server started on port {port}")
