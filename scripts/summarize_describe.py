"""Compact, human-readable map of a describe_project.jsx dump.

Usage:
    python scripts/summarize_describe.py data/tmp/lom_describe.json
    python scripts/summarize_describe.py data/tmp/lom_describe.json --effects
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ONLY_TRACK: set[str] = set()


def fmt(x: float | None) -> str:
    if x is None:
        return "  ?  "
    return f"{x:7.2f}"


def summarize(path: Path, show_effects: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    proj = data.get("project", {})
    seq = data.get("sequence", {})

    print(f"== {path.name} ==")
    print(f"Premiere: {proj.get('premiereVersion')}  project={proj.get('name')}")
    print(
        f"Sequence: {seq.get('name')!r}  "
        f"end={fmt(seq.get('end_sec'))}s  "
        f"{seq.get('frame_width')}x{seq.get('frame_height')}  "
        f"V={seq.get('videoTrackCount')} A={seq.get('audioTrackCount')}"
    )
    markers = data.get("markers", [])
    print(f"Markers: {len(markers)}")
    for m in markers:
        print(f"  @ {fmt(m.get('start_sec'))}s  {m.get('name')!r}  {m.get('comments')!r}")

    def dump_tracks(key: str, kind: str) -> None:
        for t in data.get(key, []):
            if ONLY_TRACK and t.get("label") not in ONLY_TRACK:
                continue
            clips = t.get("clips", [])
            flags = []
            if t.get("isMuted"):
                flags.append("MUTED/HIDDEN")
            if t.get("isLocked"):
                flags.append("LOCKED")
            flag_s = ("  [" + ",".join(flags) + "]") if flags else ""
            print(
                f"\n{t.get('label')} ({t.get('name')!r}) "
                f"clips={len(clips)}{flag_s}"
            )
            for c in clips:
                comps = c.get("components", []) or []
                # Premiere always has intrinsic Opacity/Motion/etc.; show
                # the count plus the named effects (non-intrinsic).
                eff_names = [
                    (cm.get("displayName") or cm.get("matchName") or "?")
                    for cm in comps
                ]
                print(
                    f"  - {c.get('name')!r:<46} "
                    f"t=[{fmt(c.get('start_sec'))} -> {fmt(c.get('end_sec'))}] "
                    f"dur={fmt(c.get('duration_sec'))} "
                    f"src=[{fmt(c.get('inPoint_sec'))}->{fmt(c.get('outPoint_sec'))}] "
                    f"spd={c.get('speed')} dis={c.get('disabled')} "
                    f"fx={len(comps)}"
                )
                pi = c.get("projectItem") or {}
                if pi.get("mediaPath"):
                    print(f"      media: {pi.get('mediaPath')}")
                if show_effects and eff_names:
                    print(f"      fx: {eff_names}")

    dump_tracks("video_tracks", "video")
    dump_tracks("audio_tracks", "audio")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("describe_json", type=Path)
    ap.add_argument("--effects", action="store_true", help="List effect/component names per clip.")
    ap.add_argument("--track", action="append", default=[],
                    help="Only show these track labels (e.g. --track V7 --track A1).")
    args = ap.parse_args()
    global ONLY_TRACK
    ONLY_TRACK = set(args.track)
    summarize(args.describe_json, args.effects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
