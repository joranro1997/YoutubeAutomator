"""Recent uploads: tells the topic generator what the creator just published.

Reads the transcripts metadata (data/corpus/transcripts/<id>.json) and the
URL index (data/corpus/video_index.tsv). Returns the N most recent video
titles tagged as belonging to a given game so the topic generator can
explicitly avoid near-duplicates.

Per-game classification is keyword-based on the title — robust enough
for two well-named games (LoM and LoE).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import GameConfig
from ..paths import REPO_ROOT


_log = logging.getLogger(__name__)


@dataclass
class RecentUpload:
    video_id: str
    title: str
    upload_date: str        # YYYYMMDD as yt-dlp emits it; "" if unknown


# Optional per-slug keyword overrides. Most games are matched automatically
# from their display_name + slug (see _game_keywords); add an entry here only
# when a game needs extra aliases beyond those.
_GAME_KEYWORDS: dict[str, list[str]] = {
    "lom": ["legend of mushroom", "lom"],
    "loe": ["legend of elements", "loe"],
}


def _game_keywords(game: GameConfig) -> list[str]:
    """Title-match keywords for a game: explicit overrides if present, else
    derived from its display name + slug so a NEW game works with no code
    change."""
    if game.slug in _GAME_KEYWORDS:
        return _GAME_KEYWORDS[game.slug]
    kws = {game.display_name.lower().strip(), game.slug.lower().strip()}
    return [k for k in kws if k]


def _matches_game(title: str, game: GameConfig) -> bool:
    t = title.lower()
    # Treat ambiguous "collab" videos as relevant to BOTH games — they should
    # appear in either game's recent-uploads list.
    return any(k in t for k in _game_keywords(game))


def recent_uploads(game: GameConfig, n: int = 15) -> list[RecentUpload]:
    """Return up to N most recent uploads belonging to this game.

    Data sources, in priority order:
      1. Transcript sidecars (data/corpus/transcripts/*.json) — give us
         the upload_date for proper sorting.
      2. video_index.tsv as fallback for titles missing a transcript JSON
         (order in the TSV is newest-first from yt-dlp's channel feed).
    """
    transcripts_dir = REPO_ROOT / "data" / "corpus" / "transcripts"
    index_path = REPO_ROOT / "data" / "corpus" / "video_index.tsv"

    by_id: dict[str, RecentUpload] = {}

    if transcripts_dir.exists():
        for meta_path in transcripts_dir.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                _log.warning("could not parse %s: %s", meta_path, e)
                continue
            title = meta.get("title") or ""
            if not _matches_game(title, game):
                continue
            vid = meta.get("video_id") or meta_path.stem
            by_id[vid] = RecentUpload(
                video_id=vid,
                title=title,
                upload_date=meta.get("upload_date") or "",
            )

    if index_path.exists():
        # Use TSV ordering as a fallback when a transcript JSON wasn't built
        # for some video (e.g. transcription failure).
        for tsv_order, line in enumerate(index_path.read_text(encoding="utf-8").splitlines()):
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            vid, title = parts
            if not _matches_game(title, game):
                continue
            if vid in by_id:
                continue
            # Fake an upload date so ordering still works: prefix with 'Z' so
            # these sort after real dates. tsv_order = newest first.
            by_id[vid] = RecentUpload(
                video_id=vid,
                title=title,
                # Pad so lexicographic sort works.
                upload_date="",
            )

    items = list(by_id.values())
    items.sort(
        # Newest first; missing-date items go to the end.
        key=lambda u: u.upload_date or "00000000",
        reverse=True,
    )
    return items[:n]
