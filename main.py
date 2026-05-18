"""Main entry point for the Temperature Optimization application"""
import asyncio
import os
import logging
import signal
import sys

from aiohttp import web

from core.settings import settings
from core.app_factory import create_app
from core.utils.logging_config import setup_logging

# Configure structured logging (JSON format)
setup_logging(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    json_format=os.getenv('LOG_FORMAT', 'json') == 'json'
)
logger = logging.getLogger(__name__)

# Global shutdown event
shutdown_event = asyncio.Event()


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name} signal, initiating graceful shutdown...")
    shutdown_event.set()


async def main():
    """Main entry point for the application."""
    # Register signal handlers for graceful shutdown
    if sys.platform != 'win32':
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    # Ensure the web server listens on all interfaces, crucial for Docker
    site = web.TCPSite(runner, host='0.0.0.0', port=settings.app.port)
    
    logger.info("Starting web server and bot...")
    await site.start()
    logger.info(f"--- Server started at http://{settings.app.host}:{settings.app.port} ---")

    try:
        # Wait for shutdown signal
        await shutdown_event.wait()
        logger.info("--- Shutdown signal received, starting graceful shutdown ---")
    except KeyboardInterrupt:
        logger.info("--- Keyboard interrupt received ---")
    finally:
        logger.info("--- Shutting down application ---")
        # Stop accepting new connections
        await site.stop()
        logger.info("--- Web server stopped accepting connections ---")
        
        # Cleanup runner (this will trigger on_shutdown handlers)
        await runner.cleanup()
        logger.info("--- Application shutdown complete ---")


if __name__ == "__main__":
    # Settings are already loaded and validated in core/settings.py
    # If any required env variable is missing, it will raise ValueError on import
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped.")
