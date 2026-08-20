"""Tests for core/api/media.py — media file serving."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from core.api.media import get_media_handler, _resolve_safe_media_path
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"media api import unavailable: {_IMPORT_ERR}"
)


class FakeSettings:
    class Parser:
        events_media_dir = '/tmp/media'

    def __init__(self):
        self.parser = self.Parser()


class FakeRequest:
    def __init__(self, filename='test.jpg'):
        self.match_info = {'filename': filename}
        self._settings = FakeSettings()

    @property
    def app(self):
        return {}


async def _call(handler, request):
    return await handler(request)


class TestResolveSafeMediaPath:
    def test_rejects_path_traversal(self):
        assert _resolve_safe_media_path('/tmp', '../secret.txt') is None

    def test_rejects_non_jpg(self):
        assert _resolve_safe_media_path('/tmp', 'test.png') is None

    def test_accepts_bare_jpg(self, tmp_path):
        d = tmp_path / 'media'
        d.mkdir()
        f = d / 'photo.jpg'
        f.write_text('fake')
        import core.api.media as media_mod
        original = getattr(media_mod, 'settings', None)
        media_mod.settings = FakeSettings()
        media_mod._media_base_cache.clear()
        try:
            result = _resolve_safe_media_path(str(d), 'photo.jpg')
            assert result is not None
            assert result.exists()
        finally:
            media_mod.settings = original
            media_mod._media_base_cache.clear()

    def test_rejects_symlink_outside(self, tmp_path):
        d = tmp_path / 'media'
        d.mkdir()
        outside = tmp_path / 'secret.txt'
        outside.write_text('secret')
        link = d / 'link.jpg'
        link.symlink_to(outside)
        import core.api.media as media_mod
        original = getattr(media_mod, 'settings', None)
        media_mod.settings = FakeSettings()
        media_mod._media_base_cache.clear()
        try:
            result = _resolve_safe_media_path(str(d), 'link.jpg')
            assert result is None
        finally:
            media_mod.settings = original
            media_mod._media_base_cache.clear()


class TestGetMediaHandler:
    @pytest.mark.asyncio
    async def test_rejects_invalid_filename(self):
        resp = await _call(get_media_handler, FakeRequest(filename='../secret.txt'))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_rejects_non_jpg(self):
        resp = await _call(get_media_handler, FakeRequest(filename='test.png'))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_serves_existing_jpg(self, tmp_path):
        d = tmp_path / 'media'
        d.mkdir()
        f = d / 'photo.jpg'
        f.write_bytes(b'fake-image-bytes')
        import core.api.media as media_mod
        original = getattr(media_mod, 'settings', None)
        media_mod.settings = FakeSettings()
        media_mod._media_base_cache.clear()
        try:
            req = FakeRequest(filename='photo.jpg')
            req.match_info = {'filename': 'photo.jpg'}
            # patch the path resolution to use tmp_path
            with patch.object(media_mod, '_get_media_base', return_value=d):
                resp = await _call(get_media_handler, req)
            assert resp.status == 200
        finally:
            media_mod.settings = original
            media_mod._media_base_cache.clear()

    @pytest.mark.asyncio
    async def test_rejects_oversized_file(self, tmp_path):
        d = tmp_path / 'media'
        d.mkdir()
        f = d / 'big.jpg'
        f.write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        import core.api.media as media_mod
        from core.utils.validators import MAX_MEDIA_FILE_BYTES
        original = getattr(media_mod, 'settings', None)
        media_mod.settings = FakeSettings()
        media_mod._media_base_cache.clear()
        try:
            req = FakeRequest(filename='big.jpg')
            with patch.object(media_mod, '_get_media_base', return_value=d):
                resp = await _call(get_media_handler, req)
            assert resp.status == 413
        finally:
            media_mod.settings = original
            media_mod._media_base_cache.clear()
