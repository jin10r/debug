"""API endpoints for media files (photos)"""
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

from common.settings import settings
from core.utils.validators import MAX_MEDIA_FILE_BYTES

logger = logging.getLogger(__name__)

# Кэш resolved-пути медиа-директории: Path.resolve() — блокирующий syscall
# (stat + readlink). Директория не меняется в runtime, поэтому кэшируем
# результат один раз при первом обращении вместо вычисления на каждый запрос.
_media_base_cache: dict[str, Path] = {}


def _get_media_base(events_dir: str) -> Path:
    """Возвращает кэшированный resolved Path медиа-директории."""
    if events_dir not in _media_base_cache:
        _media_base_cache[events_dir] = Path(events_dir).resolve()
    return _media_base_cache[events_dir]


def _resolve_safe_media_path(events_dir: str, filename: str) -> Optional[Path]:
    """Resolve `filename` inside `events_dir`, or None if unsafe.

    Defense-in-depth against path traversal AND symlink escape:
      1. filename must be a bare basename (no path components, no '..');
      2. only `.jpg` is served;
      3. the RESOLVED path (resolve() follows symlinks) must stay strictly inside
         the resolved media dir — so a symlink pointing outside is rejected.
    """
    if not filename or filename != os.path.basename(filename):
        return None
    if not filename.endswith('.jpg'):
        return None
    base = _get_media_base(events_dir)
    candidate = (base / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


async def get_media_handler(request: web.Request) -> web.Response:
    """Handle media file requests (photos). Fallback for nginx alias (see nginx.conf)."""
    filename = request.match_info.get('filename') or ''
    media_path = _resolve_safe_media_path(settings.parser.events_media_dir, filename)
    if media_path is None:
        return web.json_response({'error': 'Invalid filename'}, status=403)
    if not media_path.is_file():
        return web.json_response({'error': 'File not found'}, status=404)
    try:
        file_size = media_path.stat().st_size
        if file_size > MAX_MEDIA_FILE_BYTES:
            logger.warning(f"File too large: {filename} ({file_size} bytes)")
            return web.json_response({'error': 'File too large'}, status=413)
    except OSError:
        return web.json_response({'error': 'Cannot stat file'}, status=500)
    try:
        return web.FileResponse(
            path=media_path,
            headers={
                'Cache-Control': 'public, max-age=300',
                'Content-Type': 'image/jpeg',
                'Content-Disposition': 'inline',
            },
        )
    except Exception:
        logger.exception("Failed to serve media file: %s", filename)
        return web.json_response({'error': 'Internal server error'}, status=500)


def setup_media_routes(app: web.Application):
    """Setup media file routes"""
    # Endpoint for serving media files (photos)
    app.router.add_get('/api/media/events/{filename}', get_media_handler)