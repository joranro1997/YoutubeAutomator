"""Auto-render helpers: media-cache cleanup (pure FS, no Adobe needed)."""

from pathlib import Path

import youtube_automator.adobe.auto_render as ar


def test_clear_media_cache_deletes_files_and_reports_freed(tmp_path, monkeypatch):
    files_dir = tmp_path / "Media Cache Files"
    db_dir = tmp_path / "Media Cache"
    (files_dir / "sub").mkdir(parents=True)
    db_dir.mkdir()
    (files_dir / "a.cfa").write_bytes(b"x" * 1000)
    (files_dir / "sub" / "b.pek").write_bytes(b"y" * 500)
    (db_dir / "MediaCache.db").write_bytes(b"z" * 250)

    monkeypatch.setattr(ar, "_media_cache_dirs", lambda: [files_dir, db_dir])
    monkeypatch.setattr(ar, "adobe_running", lambda: False)

    res = ar.clear_media_cache()
    assert res["ran"] is True
    assert res["freed_bytes"] == 1750
    # Files gone; the top-level cache dirs themselves remain.
    assert files_dir.exists() and db_dir.exists()
    assert not (files_dir / "a.cfa").exists()
    assert not (files_dir / "sub" / "b.pek").exists()
    assert not (db_dir / "MediaCache.db").exists()


def test_clear_media_cache_skips_when_adobe_running(tmp_path, monkeypatch):
    files_dir = tmp_path / "Media Cache Files"
    files_dir.mkdir()
    keep = files_dir / "a.cfa"
    keep.write_bytes(b"x" * 1000)

    monkeypatch.setattr(ar, "_media_cache_dirs", lambda: [files_dir])
    monkeypatch.setattr(ar, "adobe_running", lambda: True)

    res = ar.clear_media_cache()
    assert res["ran"] is False
    assert res["reason"] == "adobe_running"
    assert keep.exists()  # nothing deleted while Adobe holds the cache


def test_clear_media_cache_no_cache_dirs_is_safe(monkeypatch):
    monkeypatch.setattr(ar, "_media_cache_dirs", lambda: [])
    monkeypatch.setattr(ar, "adobe_running", lambda: False)
    res = ar.clear_media_cache()
    assert res["ran"] is True
    assert res["freed_bytes"] == 0
