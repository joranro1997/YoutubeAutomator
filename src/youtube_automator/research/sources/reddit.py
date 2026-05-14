"""Reddit source: pulls recent hot/new posts from configured subreddits via PRAW.

Read-only, app-only OAuth (script-type app). No user account needed.
"""

from __future__ import annotations

from ..types import ResearchItem
from ...config import GameConfig


def fetch(game: GameConfig) -> list[ResearchItem]:
    """Fetch recent posts from the game's configured subreddits.

    NOTE: stub. Implementation will use praw.Reddit(read_only=True), iterate
    subreddit.hot(limit=top_limit) and subreddit.new(limit=new_limit),
    skip stickies, and convert to ResearchItem.
    """
    raise NotImplementedError("Stub — implement with PRAW")
