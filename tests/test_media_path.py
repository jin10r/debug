"""Tests for core/api/media._resolve_safe_media_path (path-traversal/symlink guard)."""
import importlib
import os

media = importlib.import_module("core.api.media")
resolve = media._resolve_safe_media_path


def test_valid_basename(tmp_path):
    (tmp_path / "event_1.jpg").write_bytes(b"x")
    p = resolve(str(tmp_path), "event_1.jpg")
    assert p is not None and p.name == "event_1.jpg"


def test_rejects_non_jpg(tmp_path):
    assert resolve(str(tmp_path), "evil.png") is None
    assert resolve(str(tmp_path), "evil.jpg.exe") is None


def test_rejects_path_components(tmp_path):
    assert resolve(str(tmp_path), "../secret.jpg") is None
    assert resolve(str(tmp_path), "sub/dir.jpg") is None
    assert resolve(str(tmp_path), "/etc/passwd.jpg") is None
    assert resolve(str(tmp_path), "") is None


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"secret")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    link = media_dir / "link.jpg"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported here")
    # resolve() follows the symlink → target is outside media_dir → rejected
    assert resolve(str(media_dir), "link.jpg") is None
