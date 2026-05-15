"""Background tasks for the application"""
import asyncio
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def cleanup_photos_task(shutdown_event: asyncio.Event = None):
    """Periodically cleans up old photo files from the media directory.
    
    Args:
        shutdown_event: Optional event to check for graceful shutdown.
                       If set, the task will exit cleanly instead of waiting.
    """
    events_dir = os.getenv('EVENTS_MEDIA_DIR')
    if events_dir:
        media_dir = Path(events_dir)
    else:
        media_dir = Path('/app/media/events')
    cleanup_interval_seconds = 1800  # каждые 30 минут
    max_age_seconds = 3600          # 1 hour

    while True:
        try:
            logger.info("Running scheduled task to delete old photos...")
            now = time.time()
            deleted_count = 0
            if not media_dir.exists():
                logger.warning(f"Media directory {media_dir} does not exist. Skipping cleanup.")
            else:
                for file_path in media_dir.iterdir():
                    if file_path.is_file():
                        try:
                            if (now - file_path.stat().st_mtime) > max_age_seconds:
                                file_path.unlink()
                                logger.info(f"Deleted old photo: {file_path.name}")
                                deleted_count += 1
                        except Exception as e:
                            logger.error(f"Error processing file {file_path}: {e}")

                if deleted_count > 0:
                    logger.info(f"Photo cleanup complete. Deleted {deleted_count} files.")

        except Exception as e:
            logger.error(f"Error in photo cleanup task: {e}", exc_info=True)

        # Ждём интервал, но с проверкой shutdown_event для быстрого завершения
        if shutdown_event:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=cleanup_interval_seconds)
                logger.info("🛑 cleanup_photos_task: shutdown requested, exiting gracefully")
                return
            except asyncio.TimeoutError:
                pass  # Интервал истёк, продолжаем работу
        else:
            await asyncio.sleep(cleanup_interval_seconds)




