"""YouTube source: which topics are trending on YouTube for this game.

Uses yt-dlp's `ytsearchN:` mechanism (no API key required) to fetch the top N
results for each configured query. We extract metadata (title, channel, view
count, upload date) WITHOUT downloading any media.

Two filters applied:
- max_age_days: drop videos older than X days (we want fresh signal).
- excludes the user's own channel (no point recommending topics he already covered).

The user's channel is read from the YOUTUBE_CHANNEL_ID env var when present.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import yt_dlp

from ...config import GameConfig, get_env
from ..types import ResearchItem


_log = logging.getLogger(__name__)


def _parse_upload_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _flat_search(query: str, n: int) -> list[dict]:
    """Run a `ytsearchN:` flat-playlist extract — title/id/uploader only, no media."""
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": "ytsearch",
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    if not info:
        return []
    return info.get("entries") or []


def fetch(game: GameConfig) -> list[ResearchItem]:
    cfg = game.sources.youtube
    if not cfg.search_queries:
        return []

    own_channel = get_env().youtube_channel_id or ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.max_age_days)

    items: list[ResearchItem] = []
    seen_ids: set[str] = set()

    for query in cfg.search_queries:
        try:
            entries = _flat_search(query, cfg.results_per_query)
        except Exception as e:  # noqa: BLE001 — best-effort source
            _log.warning("youtube search %r failed: %s", query, e)
            continue

        for entry in entries:
            if not entry:
                continue
            video_id = entry.get("id") or ""
            if not video_id or video_id in seen_ids:
                continue
            channel_id = entry.get("channel_id") or entry.get("uploader_id") or ""
            if own_channel and channel_id == own_channel:
                continue  # skip user's own videos
            upload_dt = _parse_upload_date(entry.get("upload_date"))
            if upload_dt and upload_dt < cutoff:
                continue
            seen_ids.add(video_id)

            views = entry.get("view_count")
            url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
            items.append(
                ResearchItem(
                    source="youtube",
                    source_url=url,
                    source_label=f"YouTube search: {query}",
                    title=entry.get("title") or "(no title)",
                    body=entry.get("description") or "",
                    author=entry.get("uploader") or entry.get("channel") or "",
                    score=int(views) if isinstance(views, int) else None,
                    posted_at=upload_dt,
                    game_slug=game.slug,
                    tags=[query],
                )
            )

    return items
