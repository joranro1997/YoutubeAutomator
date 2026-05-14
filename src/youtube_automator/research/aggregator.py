"""Aggregator: runs all configured sources for a game and stores a snapshot.

Output: a JSON file under data/research_cache/<slug>/<yyyymmdd>.json with all
deduplicated ResearchItems. Topic generation reads from this snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import GameConfig
from ..paths import RESEARCH_CACHE_DIR
from .sources import discord as discord_src
from .sources import reddit as reddit_src
from .sources import web as web_src
from .types import ResearchItem


def run(game: GameConfig) -> Path:
    """Run all sources, dedupe, write snapshot, return path.

    NOTE: stub. Will call each source, dedupe by (source, source_url, title),
    sort by posted_at desc, write JSON.
    """
    raise NotImplementedError("Stub — implement aggregation + dedup + write")


def latest_snapshot(game: GameConfig) -> list[ResearchItem]:
    """Load the most recent snapshot for this game (or empty list)."""
    raise NotImplementedError("Stub — implement snapshot loader")
