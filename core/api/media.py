"""API endpoints for media files (photos)"""
from pathlib import Path
from aiohttp import web
from aiohttp.web_response import Response
import mimetypes

from core.settings import settings


async def get_media_handler(request: web.Request) -> web.Response:
    """Handle media file requests (photos)"""
    try:
        # Extract filename from path
        filename = request.match_info.get('filename')
        if not filename:
            return web.json_response({'error': 'Filename required'}, status=400)

        # Security: only allow .jpg files
        if not filename.endswith('.jpg'):
            return web.json_response({'error': 'Only .jpg files allowed'}, status=403)

        # Каталог медиафайлов — централизованно из settings.
        events_dir = settings.parser.events_media_dir
        media_path = Path(events_dir) / filename
        
        # Security: prevent path traversal
        if not media_path.resolve().is_relative_to(Path(events_dir).resolve()):
            return web.json_response({'error': 'Invalid file path'}, status=403)
        
        # Check if file exists
        if not media_path.exists() or not media_path.is_file():
            return web.json_response({'error': 'File not found'}, status=404)
        
        # Determine content type
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        
        # Serve file with appropriate headers for browser caching
        return web.FileResponse(
            path=media_path,
            headers={
                'Content-Type': content_type,
                'Cache-Control': 'public, max-age=300'  # 5 minutes cache
            }
        )
        
    except Exception as e:
        return web.json_response({'error': f'Internal server error: {str(e)}'}, status=500)


def setup_media_routes(app: web.Application):
    """Setup media file routes"""
    # Endpoint for serving media files (photos)
    app.router.add_get('/api/media/events/{filename}', get_media_handler)