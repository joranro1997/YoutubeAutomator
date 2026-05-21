"""Resolve the on-disk media path for each clip our rebuild actually placed
(nest interior video + master V7 + master A1). ASCII-safe output.

    python scripts/verify_media_paths.py data/outputs/lom/guideline/guideline.prproj
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from youtube_automator.adobe.prproj_xml import Project

SEQ = "2023-03-23 20-59-52"


def media_path_of(p: Project, clipref) -> str:
    """clip -> SubClip -> MasterClip(UID) -> ClipProjectItem? No: MasterClip
    -> (its Clips) -> Source -> MediaSource -> Media -> FilePath. Simpler:
    follow the trackitem's own SubClip -> Clip -> Source -> Media."""
    ti = clipref._ti
    # walk up: the VideoClipTrackItem owning this TrackItem
    # (we stored _clip = the inner Clip with InPoint/OutPoint)
    clip = clipref._clip
    if clip is None:
        return "(no clip)"
    src = clip.find("Source")
    ms = p._by_id.get(src.get("ObjectRef")) if src is not None and src.get("ObjectRef") else None
    if ms is None:
        return "(no source)"
    mref = ms.find("MediaSource/Media")
    media = p._by_id.get(mref.get("ObjectURef")) if mref is not None else None
    if media is None:
        return "(no media)"
    return (media.findtext("ActualMediaFilePath") or media.findtext("FilePath") or "?")


def safe(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def main() -> int:
    out = Path(sys.argv[1])
    p = Project.load(out)
    for seqname in (SEQ, "GAMEPLAY_NEST"):
        m = p.map_sequence(seqname)
        print(f"\n== {seqname} ==")
        for lbl in ("V7", "A1"):
            if lbl not in m:
                continue
            print(f"-- {lbl} --")
            for c in m[lbl]:
                print(f"  {safe(str(c.name))[:22]:22} "
                      f"[{(c.start_sec or 0):7.1f}-{(c.end_sec or 0):7.1f}]  "
                      f"{safe(media_path_of(p, c))}")
        # nest interior content track
        if seqname == "GAMEPLAY_NEST":
            for lbl, clips in m.items():
                if lbl.startswith("V") and clips and lbl != "V7":
                    print(f"-- {lbl} (nest content) --")
                    for c in clips:
                        print(f"  {safe(str(c.name))[:22]:22} {safe(media_path_of(p, c))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
