"""Photoshop thumbnail automation via ExtendScript generation.

Strategy:
- The user's .psd templates use Smart Objects for the title text and the
  featured screenshot, plus Variables for any secondary text.
- This module emits a .jsx that, when run inside Photoshop, will:
    1. Open the template .psd.
    2. Replace the title Smart Object's text contents.
    3. Replace the featured-image Smart Object with the chosen gameplay still.
    4. Export PNG (1280x720) to the configured outputs dir.

Runs on Windows only.
"""

from __future__ import annotations

from pathlib import Path

from ..config import GameConfig


def render(
    *,
    thumbnail_copy: str,
    featured_image: Path,
    template_path: Path,
    output_png: Path,
) -> Path:
    """Generate the .jsx and (optionally) launch Photoshop to run it.

    NOTE: stub.
    """
    raise NotImplementedError("Stub — implement ExtendScript generation for Photoshop")
