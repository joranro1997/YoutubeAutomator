"""Premiere automation via ExtendScript generation.

Strategy:
- The user keeps his existing .prproj templates and .mogrt motion graphics.
- This module emits a .jsx file that, when run inside Premiere, will:
    1. Open the template project.
    2. Replace placeholder clips with the recorded gameplay clips.
    3. Splice in the pre-recorded Aptoide ad segment at its marker.
    4. Fill in lower-third texts from script.segments[].text.
    5. Export to MP4 via Adobe Media Encoder (queue + start).

Runs on Windows only. Invoke Premiere with:
    "Adobe Premiere Pro.exe" --runScript <generated_script>.jsx
"""

from __future__ import annotations

from pathlib import Path

from ..script.generator import Script
from ..config import GameConfig


def render(
    *,
    script: Script,
    game: GameConfig,
    gameplay_clips: list[Path],
    output_mp4: Path,
) -> Path:
    """Generate the .jsx and (optionally) launch Premiere to run it.

    NOTE: stub. Will template a .jsx using string-builder (ExtendScript is ES3),
    write it to data/tmp/, and return its path. A second helper will invoke
    Premiere on Windows.
    """
    raise NotImplementedError("Stub — implement ExtendScript generation for Premiere")
