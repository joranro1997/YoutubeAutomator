"""Reddit source: pulls recent hot+new posts from configured subreddits via PRAW.

Read-only, app-only OAuth (script-type app). No user account needed.
Requires REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT in env.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import praw

from ...config import GameConfig, get_env
from ..types import ResearchItem


_log = logging.getLogger(__name__)


def _client() -> praw.Reddit | None:
    env = get_env()
    if not (env.reddit_client_id and env.reddit_client_secret):
        _log.warning("Reddit credentials not set — skipping reddit source")
        return None
    return praw.Reddit(
        client_id=env.reddit_client_id,
        client_secret=env.reddit_client_secret,
        user_agent=env.reddit_user_agent,
        check_for_async=False,
    )


def fetch(game: GameConfig) -> list[ResearchItem]:
    cfg = game.sources.reddit
    if not cfg.subreddits:
        return []
    reddit = _client()
    if reddit is None:
        return []

    items: list[ResearchItem] = []
    seen_ids: set[str] = set()

    for sub_name in cfg.subreddits:
        sub = reddit.subreddit(sub_name)

        for kind, listing in (
            ("hot", sub.hot(limit=cfg.top_limit)),
            ("new", sub.new(limit=cfg.new_limit)),
        ):
            try:
                for post in listing:
                    if post.stickied:
                        continue
                    if post.id in seen_ids:
                        continue
                    seen_ids.add(post.id)
                    items.append(
                        ResearchItem(
                            source="reddit",
                            source_url=f"https://www.reddit.com{post.permalink}",
                            source_label=f"r/{sub_name} ({kind})",
                            title=post.title,
                            body=(post.selftext or "")[:2000],
                            author=str(post.author) if post.author else "",
                            score=int(post.score or 0),
                            posted_at=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                            game_slug=game.slug,
                            tags=[f"r/{sub_name}", kind],
                        )
                    )
            except Exception as e:  # noqa: BLE001
                _log.warning("reddit fetch r/%s %s failed: %s", sub_name, kind, e)

    return items
