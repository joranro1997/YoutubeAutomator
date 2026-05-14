"""Style corpus loader.

The user records his own voice. To make Claude write scripts that sound like
him (and not like generic AI), we feed it a sample of his past transcripts.

Transcripts are pulled with `yt-dlp` + `faster-whisper` by a separate ingest
script (scripts/ingest_transcripts.py) and stored as plaintext under
data/corpus/transcripts/<video_id>.txt.

This module:
- loads them
- samples representative excerpts (avoid blowing the context window)
- exposes a `style_prompt()` that gets injected into script generation
"""

from __future__ import annotations

from pathlib import Path

from ..paths import REPO_ROOT
from ..config import get_settings


def load_all() -> list[tuple[str, str]]:
    """Return (video_id, transcript) tuples for every transcript on disk."""
    corpus_dir = REPO_ROOT / get_settings().channel.style_corpus_dir
    if not corpus_dir.exists():
        return []
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(corpus_dir.glob("*.txt"))]


def style_prompt(max_chars: int = 12000) -> str:
    """Build a 'speak like this' prompt segment from the corpus.

    NOTE: stub. Will sample diverse excerpts (intro/midbody/outro), respecting
    the char budget, and format as a quoted reference block.
    """
    raise NotImplementedError("Stub — implement sampling + prompt assembly")
