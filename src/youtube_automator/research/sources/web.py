"""Web source: patch-notes pages, wiki pages, official site news.

Polite scraping only (User-Agent, rate limit, robots.txt respected).
"""

from __future__ import annotations

from ..types import ResearchItem
from ...config import GameConfig


def fetch(game: GameConfig) -> list[ResearchItem]:
    """Scrape configured patch-notes URLs and the wiki root for this game.

    For LoM and LoE these games publish updates only via Discord — there are
    no official patch-notes web pages — so this source returns nothing today.
    The module is kept as a stub to make adding URLs trivial in the future.
    """
    if not game.sources.web.patch_notes_urls and not game.sources.web.wiki_root:
        return []
    # TODO: real implementation when URLs are added.
    return []
