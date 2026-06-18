"""API endpoints for media files (photos)"""
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

from core.settings import settings

logger = logging.getLogger(__name__)


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
    base = Path(events_dir).resolve()
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
        # FileResponse infers Content-Type from the extension (image/jpeg).
        return web.FileResponse(
            path=media_path,
            headers={'Cache-Control': 'public, max-age=300'},
        )
    except Exception:
        logger.exception("Failed to serve media file: %s", filename)
        return web.json_response({'error': 'Internal server error'}, status=500)


def setup_media_routes(app: web.Application):
    """Setup media file routes"""
    # Endpoint for serving media files (photos)
    app.router.add_get('/api/media/events/{filename}', get_media_handler)