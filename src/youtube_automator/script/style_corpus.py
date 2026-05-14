"""Style corpus loader and sampler.

To make Claude write scripts that sound like the user, we feed it excerpts
from his own video transcripts. Transcripts are produced by
`yta ingest-transcripts` (see transcribe/whisper_runner.py) and stored at
data/corpus/transcripts/<video_id>.txt.

`style_prompt()` builds a quoted reference block that fits within a token
budget — it samples diverse excerpts (intro / mid / outro) from a random
subset of transcripts, biased toward the most recent.

The corpus typically grows to 30+ videos (~50k–150k words). We can't pass it
all every call; we sample ~12k chars by default, which is around 3k tokens —
small enough to fit comfortably and cache.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from ..config import get_settings
from ..paths import REPO_ROOT


def _corpus_dir() -> Path:
    return REPO_ROOT / get_settings().channel.style_corpus_dir


def load_all() -> list[tuple[str, str]]:
    """Return (video_id, transcript) tuples for every transcript on disk."""
    d = _corpus_dir()
    if not d.exists():
        return []
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.txt"))]


def _newest_first() -> list[Path]:
    """Order transcripts by upload_date (from sibling .json), newest first."""
    d = _corpus_dir()
    if not d.exists():
        return []
    items: list[tuple[str, Path]] = []
    for p in d.glob("*.txt"):
        meta = d / f"{p.stem}.json"
        upload = ""
        if meta.exists():
            try:
                upload = json.loads(meta.read_text(encoding="utf-8")).get("upload_date") or ""
            except Exception:  # noqa: BLE001
                pass
        items.append((upload, p))
    items.sort(reverse=True)
    return [p for _, p in items]


def _sample_segments(text: str, k: int = 3, seg_chars: int = 1200) -> list[str]:
    """Take k roughly evenly-spaced segments from a transcript."""
    n = len(text)
    if n <= seg_chars * k:
        return [text]
    out: list[str] = []
    for i in range(k):
        start = (n * i) // k + random.randint(0, max(0, (n // k) - seg_chars))
        out.append(text[start : start + seg_chars])
    return out


def style_prompt(max_chars: int = 12000, max_videos: int = 8, seed: int | None = 7) -> str:
    """Build a 'speak like this' prompt segment from the corpus.

    Deterministic by default (seed=7) so prompt caching pays off — the same
    excerpts will be reused across calls and Claude can cache them. Pass
    seed=None to get a fresh sample (no cache benefit).
    """
    paths = _newest_first()
    if not paths:
        return ""

    rng = random.Random(seed)
    chosen = paths[: max_videos * 2]
    rng.shuffle(chosen)
    chosen = chosen[:max_videos]

    blocks: list[str] = []
    used = 0
    header = (
        "Below are excerpts from the creator's own past videos. Mimic this voice:\n"
        "energy level, vocabulary, hooks, sentence rhythm, slang, audience-address.\n"
        "Do not copy specific facts from these excerpts.\n\n"
    )
    blocks.append(header)
    used += len(header)

    for p in chosen:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        segments = _sample_segments(text)
        for seg in segments:
            entry = f"--- excerpt from video {p.stem} ---\n{seg.strip()}\n\n"
            if used + len(entry) > max_chars:
                return "".join(blocks).rstrip()
            blocks.append(entry)
            used += len(entry)

    return "".join(blocks).rstrip()
