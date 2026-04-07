"""Cache management API handlers"""
import hashlib
import json
import os
import time
from pathlib import Path
from aiohttp import web
from typing import Dict


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    if not file_path.exists():
        return ""
    
    hash_sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Read file in chunks to handle large files efficiently
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except Exception:
        return ""


def collect_assets_hashes(assets_dir: Path) -> Dict[str, str]:
    """Collect hashes for all assets in the directory"""
    assets_hashes = {}
    
    # Walk through the assets directory
    for root, dirs, files in os.walk(assets_dir):
        for file in files:
            file_path = Path(root) / file
            # Only include certain file types for caching
            if file_path.suffix.lower() in ['.js', '.css', '.html', '.svg', '.jpg', '.jpeg', '.png', '.gif', '.ico']:
                relative_path = file_path.relative_to(assets_dir)  # Relative to ./web
                web_path = f'/{relative_path.as_posix()}'
                assets_hashes[web_path] = calculate_file_hash(file_path)
    
    return assets_hashes


async def get_cache_manifest_handler(request: web.Request):
    """Return the current cache manifest with file hashes"""
    # Поддержка как GET, так и POST запросов
    if request.method == 'POST':
        try:
            await request.json()  # Просто проверяем, что тело - JSON
        except Exception:
            pass  # Игнорируем ошибки при чтении тела POST-запроса

    # Define the assets directory path
    web_dir = Path('./web').resolve()

    if not web_dir.exists():
        return web.json_response({'error': 'Web directory not found'}, status=500)

    # Collect hashes for all assets
    assets_hashes = collect_assets_hashes(web_dir)

    # Calculate overall manifest hash
    manifest_content = json.dumps(assets_hashes, sort_keys=True)
    manifest_hash = hashlib.sha256(manifest_content.encode()).hexdigest()

    manifest = {
        'manifest_hash': manifest_hash,
        'timestamp': int(time.time()),
        'assets': assets_hashes
    }

    resp = web.json_response(manifest)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


async def get_cache_status_handler(request: web.Request):
    """Return cache status information"""
    # Поддержка как GET, так и POST запросов
    if request.method == 'POST':
        try:
            await request.json()  # Просто проверяем, что тело - JSON
        except Exception:
            pass  # Игнорируем ошибки при чтении тела POST-запроса

    # Define the assets directory path
    web_dir = Path('./web').resolve()

    if not web_dir.exists():
        return web.json_response({'error': 'Web directory not found'}, status=500)

    # Collect hashes for all assets
    assets_hashes = collect_assets_hashes(web_dir)

    # Calculate overall manifest hash
    manifest_content = json.dumps(assets_hashes, sort_keys=True)
    manifest_hash = hashlib.sha256(manifest_content.encode()).hexdigest()

    status = {
        'manifest_hash': manifest_hash,
        'asset_count': len(assets_hashes),
        'timestamp': int(time.time())
    }

    resp = web.json_response(status)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


async def check_cache_update_handler(request: web.Request):
    """Check if cache update is needed based on provided client manifest hash"""
    # Поддержка как GET, так и POST запросов
    if request.method == 'POST':
        try:
            data = await request.json()
        except:
            # If no manifest hash provided, assume client needs the current manifest
            data = {}
    else:
        # For GET requests, use empty data
        data = {}

    # Define the assets directory path
    web_dir = Path('./web').resolve()

    if not web_dir.exists():
        return web.json_response({'error': 'Web directory not found'}, status=500)

    # Collect hashes for all assets
    assets_hashes = collect_assets_hashes(web_dir)

    # Calculate overall manifest hash
    manifest_content = json.dumps(assets_hashes, sort_keys=True)
    server_manifest_hash = hashlib.sha256(manifest_content.encode()).hexdigest()

    response_data = {
        'server_manifest_hash': server_manifest_hash,
        'assets_update_needed': data.get('manifest_hash', '') != server_manifest_hash,
        'events_update_needed': False,
        'update_needed': (data.get('manifest_hash', '') != server_manifest_hash),
        'timestamp': int(time.time())
    }

    resp = web.json_response(response_data)
    resp.headers['Cache-Control'] = 'no-store'
    return resp