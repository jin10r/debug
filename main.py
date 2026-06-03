"""Main entry point for the Temperature Optimization application"""
import asyncio
import logging
import signal

from aiohttp import web

from core.settings import settings
from core.app_factory import create_app
from core.utils.logging_config import setup_logging

# Configure structured logging — параметры из централизованного settings.
setup_logging(
    level=getattr(logging, settings.app.log_level.upper(), logging.INFO),
    json_format=settings.app.log_format == 'json',
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point for the application."""
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: (
                logger.info(f"Received {signal.Signals(s).name}, initiating graceful shutdown..."),
                shutdown_event.set(),
            )
        )

    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=settings.app.port)

    logger.info("Starting web server and bot...")
    await site.start()
    logger.info(f"--- Server started at http://{settings.app.host}:{settings.app.port} ---")

    await shutdown_event.wait()
    logger.info("--- Shutdown signal received, starting graceful shutdown ---")

    logger.info("--- Shutting down application ---")
    await site.stop()
    logger.info("--- Web server stopped accepting connections ---")
    await runner.cleanup()
    logger.info("--- Application shutdown complete ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped.")
