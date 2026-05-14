"""YouTube Data API v3 upload client.

OAuth-based (one-time browser consent on first run; token cached). Supports:
- video upload (resumable)
- thumbnail set
- scheduled publish (status.publishAt)
- categoryId / tags / description / language
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import GameConfig
from ..metadata.generator import VideoMetadata


@dataclass
class UploadResult:
    video_id: str
    url: str


def upload(
    *,
    video_path: Path,
    thumbnail_path: Path,
    metadata: VideoMetadata,
    chosen_title: str,
    game: GameConfig,
    publish_at: datetime | None = None,
    privacy_status: str = "private",   # "private" -> user reviews -> goes public/scheduled
) -> UploadResult:
    """Upload + set thumbnail + (optionally) schedule.

    NOTE: stub. Default to private so the user reviews on YouTube Studio before
    going live. Production runs may switch to publish_at for scheduling.
    """
    raise NotImplementedError("Stub — implement YouTube Data API client")
