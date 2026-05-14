"""Aggregator: runs all configured sources for a game and stores a snapshot.

Output: data/research_cache/<slug>/<yyyymmdd-HHMMSS>.json with deduplicated
ResearchItems, plus a stable `latest.json` symlink/file pointing at the most
recent snapshot.

Sources run sequentially (network calls dominate; parallelism is not worth
the complexity for 4 sources). Each source is best-effort: a failure logs a
warning and the aggregator continues.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import GameConfig
from ..paths import RESEARCH_CACHE_DIR, ensure_dirs
from .sources import discord as discord_src
from .sources import reddit as reddit_src
from .sources import web as web_src
from .sources import youtube as youtube_src
from .types import ResearchItem


_log = logging.getLogger(__name__)


def _dedupe(items: list[ResearchItem]) -> list[ResearchItem]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ResearchItem] = []
    for it in items:
        key = (it.source, it.source_url or "", it.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _snapshot_dir(game: GameConfig) -> Path:
    d = RESEARCH_CACHE_DIR / game.slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(game: GameConfig) -> Path:
    """Run all sources, dedupe, write snapshot, update `latest.json`. Return path."""
    ensure_dirs()
    items: list[ResearchItem] = []

    for name, src in (
        ("reddit", reddit_src),
        ("youtube", youtube_src),
        ("discord", discord_src),
        ("web", web_src),
    ):
        try:
            fetched = src.fetch(game)
            _log.info("source %s: %d items", name, len(fetched))
            items.extend(fetched)
        except NotImplementedError:
            _log.debug("source %s is a stub — skipping", name)
        except Exception as e:  # noqa: BLE001 — never let one source kill the run
            _log.warning("source %s failed: %s", name, e)

    items = _dedupe(items)
    # Newest first, falling back to no-date items at the end.
    items.sort(
        key=lambda it: it.posted_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    sdir = _snapshot_dir(game)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = sdir / f"{stamp}.json"
    out.write_text(
        json.dumps([it.model_dump(mode="json") for it in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Maintain a stable pointer to the latest snapshot.
    latest = sdir / "latest.json"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    try:
        latest.symlink_to(out.name)
    except OSError:
        # Filesystem doesn't support symlinks (Windows without dev mode) — copy instead.
        latest.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")

    _log.info("snapshot written: %s (%d items)", out, len(items))
    return out


def latest_snapshot(game: GameConfig) -> list[ResearchItem]:
    """Load the most recent snapshot for this game (or empty list)."""
    sdir = _snapshot_dir(game)
    latest = sdir / "latest.json"
    if not latest.exists():
        # Fallback: find the alphabetically last yyyy*.json
        candidates = sorted(sdir.glob("2*.json"))
        if not candidates:
            return []
        latest = candidates[-1]
    raw = json.loads(latest.read_text(encoding="utf-8"))
    return [ResearchItem.model_validate(o) for o in raw]
