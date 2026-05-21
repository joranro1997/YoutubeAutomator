"""Persistent queue of social posts scheduled to fire alongside a YouTube
upload. The daemon (`yta social-daemon`) drains items whose `post_at`
has passed; posted items are kept (status=posted) for traceability.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..paths import OUTPUTS_DIR

QUEUE_FILE = OUTPUTS_DIR / "social_queue.json"


class SocialPost(BaseModel):
    game: str
    video_slug: str
    channel: Literal["discord", "twitter"]
    content: str
    post_at: datetime          # UTC
    video_url: str = ""
    status: Literal["pending", "posted", "failed"] = "pending"
    posted_at: datetime | None = None
    error: str = ""


class SocialQueue(BaseModel):
    posts: list[SocialPost] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path = QUEUE_FILE) -> "SocialQueue":
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path = QUEUE_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    def add(self, post: SocialPost) -> None:
        self.posts.append(post)

    def due(self, now: datetime) -> list[SocialPost]:
        return [p for p in self.posts if p.status == "pending" and p.post_at <= now]


def build_companion_posts(
    *,
    game_slug: str,
    video_slug: str,
    video_url: str,
    title: str,
    tags: list[str],
    post_at: datetime,
) -> list[SocialPost]:
    """Make a Discord + Twitter post from the upload's title + tags.

    The catchphrase is the title (which varies per video). Twitter gets a
    hashtag tail from the tags. Both fire at the same publishAt as YouTube.
    """
    # Discord auto-embeds YouTube links -> message body stays terse.
    discord_body = f"{title}\n{video_url}"

    # Twitter has a 280-char budget. Reserve room for the URL (23) + 2 newlines.
    budget = 280 - 23 - 4
    tag_text = " ".join(f"#{t.replace(' ', '')}" for t in tags[:5])
    head = title
    if len(head) + len(tag_text) > budget:
        head = head[: budget - len(tag_text) - 1].rstrip() + "..."
    twitter_body = f"{head}\n\n{video_url}\n\n{tag_text}".strip()

    return [
        SocialPost(game=game_slug, video_slug=video_slug, channel="discord",
                   content=discord_body, video_url=video_url, post_at=post_at),
        SocialPost(game=game_slug, video_slug=video_slug, channel="twitter",
                   content=twitter_body, video_url=video_url, post_at=post_at),
    ]
