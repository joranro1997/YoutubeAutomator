"""Web source: patch-notes pages, wiki pages, official site news.

Polite scraping only (User-Agent, rate limit, robots.txt respected).
"""

from __future__ import annotations

from ..types import ResearchItem
from ...config import GameConfig


def fetch(game: GameConfig) -> list[ResearchItem]:
    """Scrape configured patch-notes URLs and the wiki root for this game.

    NOTE: stub. Will use httpx + BeautifulSoup, dedupe by URL, store raw HTML
    snapshots under data/research_cache/web/<slug>/ for traceability.
    """
    raise NotImplementedError("Stub — implement with httpx + BeautifulSoup")
