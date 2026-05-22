"""Centralized, cross-platform path resolution.

Dev runs on macOS; production runs on Windows. All filesystem paths must go
through this module so that OS-specific roots (Adobe templates, shared assets)
can be overridden per machine via environment variables.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Repo root: src/youtube_automator/paths.py -> repo root is 2 parents up from src/
REPO_ROOT = Path(__file__).resolve().parents[2]

# Minimum free disk space (GiB) we require before kicking off a render. A
# 1080p60 ~15-min H.264 export plus AME's preview/conformed-audio scratch
# files needs several GiB of headroom; below this the export dies mid-way
# with a cryptic "Error compiling movie" (we hit exactly that at 5 GiB free).
MIN_RENDER_FREE_GB = 10.0

CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
SECRETS_DIR = REPO_ROOT / "secrets"

CORPUS_DIR = DATA_DIR / "corpus"
TRANSCRIPTS_DIR = CORPUS_DIR / "transcripts"
RESEARCH_CACHE_DIR = DATA_DIR / "research_cache"
OUTPUTS_DIR = DATA_DIR / "outputs"
TMP_DIR = DATA_DIR / "tmp"


def assets_root() -> Path:
    """Where shared media assets (clips, renders, templates) live.

    On Windows this should point to the user's local assets folder (e.g. a
    OneDrive/Dropbox-synced directory). On Mac dev, defaults to ./assets in repo.
    """
    override = os.getenv("ASSETS_ROOT")
    if override:
        return Path(override)
    return REPO_ROOT / "assets"


def recordings_root() -> Path:
    """Where the user drops recorded gameplay fragments, one folder per video.

    Layout: <recordings_root>/<game_slug>/<video_slug>/NNN_*.mp4
    Override per machine via RECORDINGS_ROOT. Defaults under assets_root().
    """
    override = os.getenv("RECORDINGS_ROOT")
    if override:
        return Path(override)
    return assets_root() / "recordings"


def aptoide_ads_dir() -> Path:
    """Pre-recorded Aptoide promo segments: <dir>/<slug>.mp4 (Windows-only)."""
    override = os.getenv("APTOIDE_ADS_DIR")
    if override:
        return Path(override)
    return assets_root() / "aptoide_ads"


def premiere_templates_dir() -> Path:
    """Adobe Premiere .prproj / .mogrt template directory (Windows-only at runtime)."""
    override = os.getenv("PREMIERE_TEMPLATES_DIR")
    if override:
        return Path(override)
    return assets_root() / "premiere_templates"


def photoshop_templates_dir() -> Path:
    """Adobe Photoshop .psd template directory (Windows-only at runtime)."""
    override = os.getenv("PHOTOSHOP_TEMPLATES_DIR")
    if override:
        return Path(override)
    return assets_root() / "photoshop_templates"


def ensure_dirs() -> None:
    """Create the writable data directories on first run."""
    for d in (CORPUS_DIR, TRANSCRIPTS_DIR, RESEARCH_CACHE_DIR, OUTPUTS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def free_space_gb(path: Path | str | None = None) -> float:
    """Free disk space (GiB) on the volume holding `path` (default: outputs).

    Walks up to the nearest existing ancestor so it works before the target
    directory has been created.
    """
    p = Path(path) if path is not None else OUTPUTS_DIR
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(str(p)).free / (1024 ** 3)
    except OSError:
        return float("inf")  # can't tell -> don't block
