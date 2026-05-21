"""M2b de-risk: prove Premiere accepts a cloned+repathed media cluster.

Copies a guideline take to a NEW path/name, clones its media cluster
repathed to that copy, repoints the nest-interior clips of that take, and
saves a project to open in Premiere. If those clips play from the new path
(not offline), clone+repath is sound and full M2b is just wiring.
"""
import shutil
import sys
from pathlib import Path

from youtube_automator.adobe.edit_plan import probe_duration_sec
from youtube_automator.adobe.prproj_xml import Project

SRC = Path("assets/guideline_video_fragments/2026-05-04 19-24-23.mp4")
COPY = Path("data/tmp/DERISK_renamed_take.mp4").resolve()
TPL = Path("assets/premiere_templates/lom_nest.prproj")
OUT = Path("data/outputs/_derisk/derisk_inject.prproj")


def main() -> int:
    COPY.parent.mkdir(parents=True, exist_ok=True)
    if not COPY.exists():
        shutil.copy2(SRC, COPY)
    dur = probe_duration_sec(COPY)
    print(f"copy: {COPY}  dur={dur:.2f}s")

    p = Project.load(TPL)
    media = p.clone_media_cluster("2026-05-04 19-24-23.mp4", COPY, dur)
    print("injected media:", media)

    nest = p.sequence("GAMEPLAY_NEST")
    repointed = 0
    for _lbl, ct in p.tracks(nest, "video"):
        ci = ct.find(".//ClipItems")
        tis = ci.find("TrackItems") if ci is not None else None
        if tis is None:
            continue
        for entry in tis.findall("TrackItem"):
            vti = p._deref(entry)
            cti = vti.find("ClipTrackItem") if vti is not None else None
            if cti is None:
                continue
            sub = p._resolve(cti, "SubClip")
            nm = sub.findtext("Name") if sub is not None else ""
            if nm and "19-24-23" in nm:
                p.repoint_clip_media(vti, media)
                repointed += 1
    print(f"repointed {repointed} nest clip(s) of 19-24-23 -> injected copy")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    p.save(OUT)
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
