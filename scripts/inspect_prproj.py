"""Decompress and summarise an Adobe Premiere .prproj template.

The .prproj file is gzipped XML using Premiere's ObjectID/ObjectRef indirection.
This script extracts the bits we care about for Phase 3 ExtendScript generation:

    * Project-level Sequence list (Name, Duration, FPS).
    * For the chosen sequence:
        - Track list (V1..Vn, A1..An) with track type and target reference.
        - Clip items per track: name, start, end, duration (in ticks).
        - Sequence markers (name, time).

Usage:
    python scripts/inspect_prproj.py assets/premiere_templates/lom.prproj
    python scripts/inspect_prproj.py assets/premiere_templates/lom.prproj --sequence-index 0 --json out.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# Premiere stores time as "ticks". 1 second = 254016000000 ticks.
TICKS_PER_SECOND = 254016000000


def decompress(prproj_path: Path) -> bytes:
    raw = prproj_path.read_bytes()
    # Premiere files are always gzipped; magic bytes 1f 8b.
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def ticks_to_seconds(ticks_str: str | None) -> Optional[float]:
    if not ticks_str:
        return None
    try:
        return int(ticks_str) / TICKS_PER_SECOND
    except (TypeError, ValueError):
        return None


def index_by_object_id(root: ET.Element) -> dict[str, ET.Element]:
    """Premiere flattens definitions with TWO id systems: numeric ObjectID and
    UUID-style ObjectUID. Pointers use ObjectRef and ObjectURef respectively.
    Both must be indexed and followed.
    """
    out: dict[str, ET.Element] = {}
    for el in root.iter():
        for attr in ("ObjectID", "ObjectUID"):
            oid = el.get(attr)
            if oid:
                out[oid] = el
    return out


def resolve(el: ET.Element, by_id: dict[str, ET.Element]) -> ET.Element | None:
    """If `el` is a ref pointer, return the target definition; else return None."""
    for attr in ("ObjectRef", "ObjectURef"):
        ref = el.get(attr)
        if ref:
            target = by_id.get(ref)
            if target is not None:
                return target
    return None


def child_def(parent: ET.Element, tag: str, by_id: dict[str, ET.Element]) -> ET.Element | None:
    """Find a child by tag, then follow its ObjectRef to the actual definition."""
    child = parent.find(tag)
    if child is None:
        return None
    target = resolve(child, by_id)
    return target if target is not None else child


def find_name(el: ET.Element, by_id: dict[str, ET.Element]) -> str | None:
    """Premiere stores names in various places; try the obvious ones."""
    direct = el.findtext("Name")
    if direct:
        return direct
    # Sometimes Name lives behind a ref.
    name_el = el.find("Name")
    if name_el is not None:
        target = resolve(name_el, by_id)
        if target is not None and target.text:
            return target.text
    # Try MediaSource > Source > Path (for media-backed clips, the filename).
    return None


def collect_sequences(root: ET.Element, by_id: dict[str, ET.Element]) -> list[ET.Element]:
    """Sequences with concrete content (have ObjectID or ObjectUID)."""
    return [
        el
        for el in root.iter("Sequence")
        if el.get("ObjectID") or el.get("ObjectUID")
    ]


def walk_tracks(track_group: ET.Element, by_id: dict[str, ET.Element]) -> list[ET.Element]:
    """Track refs inside a VideoTrackGroup / AudioTrackGroup -> resolved Track defs."""
    out: list[ET.Element] = []
    # Tracks are usually under a child like <Tracks> with multiple <Track ObjectRef=.../>
    for track_ref in track_group.iter("Track"):
        if track_ref.get("ObjectRef"):
            tgt = resolve(track_ref, by_id)
            if tgt is not None and tgt not in out:
                out.append(tgt)
    return out


def walk_clip_items(track: ET.Element, by_id: dict[str, ET.Element]) -> list[dict]:
    """Find every ClipTrackItem (clip on the timeline) in a Track."""
    items: list[dict] = []
    # ClipTrackItem references are typically nested; let's just iterate
    # and look for anything that looks like a clip placement on the track.
    for el in track.iter():
        tag = el.tag
        if tag not in {"ClipTrackItem", "VideoClipTrackItem", "AudioClipTrackItem"}:
            continue
        # ClipTrackItem may itself be a ref; resolve.
        node = resolve(el, by_id) or el
        # Time fields commonly present:
        start = (
            node.findtext("Start")
            or node.findtext("StartTime")
            or node.findtext("Position")
        )
        end = node.findtext("End") or node.findtext("EndTime")
        duration = node.findtext("Duration")
        in_pt = node.findtext("InPoint") or node.findtext("In")
        out_pt = node.findtext("OutPoint") or node.findtext("Out")

        # Drill to clip name through MasterClip/Source/Media chain.
        name = find_name(node, by_id)
        if not name:
            for tag2 in ("MasterClip", "Source", "SubClip", "ClipRef"):
                ch = node.find(tag2)
                if ch is None:
                    continue
                tgt = resolve(ch, by_id) or ch
                name = find_name(tgt, by_id)
                if name:
                    break

        items.append(
            {
                "tag": tag,
                "name": name,
                "start_sec": ticks_to_seconds(start),
                "end_sec": ticks_to_seconds(end),
                "duration_sec": ticks_to_seconds(duration),
                "in_sec": ticks_to_seconds(in_pt),
                "out_sec": ticks_to_seconds(out_pt),
            }
        )
    return items


def summarise_sequence(seq: ET.Element, by_id: dict[str, ET.Element]) -> dict:
    name = find_name(seq, by_id) or "(unnamed)"
    fps = seq.findtext(".//VideoFrameRate") or seq.findtext(".//FrameRate")
    duration = ticks_to_seconds(seq.findtext("Duration"))

    vtg = child_def(seq, "VideoTrackGroup", by_id)
    atg = child_def(seq, "AudioTrackGroup", by_id)

    video_tracks: list[dict] = []
    if vtg is not None:
        for i, t in enumerate(walk_tracks(vtg, by_id), start=1):
            video_tracks.append(
                {
                    "index": i,
                    "label": f"V{i}",
                    "object_id": t.get("ObjectID"),
                    "clips": walk_clip_items(t, by_id),
                }
            )
    audio_tracks: list[dict] = []
    if atg is not None:
        for i, t in enumerate(walk_tracks(atg, by_id), start=1):
            audio_tracks.append(
                {
                    "index": i,
                    "label": f"A{i}",
                    "object_id": t.get("ObjectID"),
                    "clips": walk_clip_items(t, by_id),
                }
            )

    # Markers on the sequence itself.
    markers: list[dict] = []
    mkr_owner = seq.find(".//Markers")
    if mkr_owner is not None:
        for mk in mkr_owner.iter("Marker"):
            target = resolve(mk, by_id) or mk
            markers.append(
                {
                    "name": find_name(target, by_id),
                    "start_sec": ticks_to_seconds(target.findtext("Start")),
                    "duration_sec": ticks_to_seconds(target.findtext("Duration")),
                    "comment": target.findtext("Comment"),
                }
            )

    return {
        "object_id": seq.get("ObjectID") or seq.get("ObjectUID"),
        "name": name,
        "frame_rate_ticks": fps,
        "duration_sec": duration,
        "video_track_count": len(video_tracks),
        "audio_track_count": len(audio_tracks),
        "video_tracks": video_tracks,
        "audio_tracks": audio_tracks,
        "markers": markers,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prproj")
    ap.add_argument("--sequence-index", type=int, default=None,
                    help="Pick a single sequence by index (default: all).")
    ap.add_argument("--json", type=Path, default=None,
                    help="Write the structured summary to this JSON file.")
    args = ap.parse_args()

    raw = decompress(Path(args.prproj))
    root = ET.fromstring(raw)
    by_id = index_by_object_id(root)
    seqs = collect_sequences(root, by_id)
    print(f"prproj: {args.prproj}")
    print(f"  total elements with ObjectID: {len(by_id)}")
    print(f"  sequence definitions: {len(seqs)}")
    print()

    summaries: list[dict] = []
    for i, seq in enumerate(seqs):
        if args.sequence_index is not None and i != args.sequence_index:
            continue
        s = summarise_sequence(seq, by_id)
        summaries.append({"index": i, **s})
        print(f"[Sequence {i}] {s['name']}  (ObjectID={s['object_id']})")
        print(f"  duration: {s['duration_sec']} s")
        print(f"  V tracks: {s['video_track_count']}, A tracks: {s['audio_track_count']}")
        print(f"  markers: {len(s['markers'])}")
        for vt in s["video_tracks"]:
            clip_count = len(vt["clips"])
            print(f"    {vt['label']}: {clip_count} clip(s)")
            for c in vt["clips"][:6]:
                start = c["start_sec"]
                dur = c["duration_sec"]
                print(f"      - {c['name']!r}  start={start} dur={dur}")
            if clip_count > 6:
                print(f"      ... ({clip_count - 6} more)")
        for at in s["audio_tracks"]:
            clip_count = len(at["clips"])
            print(f"    {at['label']}: {clip_count} clip(s)")
            for c in at["clips"][:4]:
                print(f"      - {c['name']!r}  start={c['start_sec']} dur={c['duration_sec']}")
            if clip_count > 4:
                print(f"      ... ({clip_count - 4} more)")
        for m in s["markers"][:8]:
            print(f"    marker: {m['name']!r} @ {m['start_sec']} s")
        print()

    if args.json:
        args.json.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
