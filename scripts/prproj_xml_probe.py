"""Reverse-engineering aid for the .prproj XML rebuild (Phase 3, approach B).

Dumps the shape (not the full content) of timeline-relevant nodes so we can
learn how Premiere encodes clip timing + media linkage.

    python scripts/prproj_xml_probe.py data/tmp/lom_nest.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TICKS = 254016000000  # ticks per second


def ssec(node: ET.Element, tag: str) -> str:
    t = node.findtext(tag)
    if t is None:
        return "-"
    try:
        return f"{int(t) / TICKS:.3f}s ({t})"
    except ValueError:
        return t


def shape(el: ET.Element, depth: int = 0, max_depth: int = 2) -> list[str]:
    out: list[str] = []
    attrs = " ".join(f'{k}={v!r}' for k, v in el.attrib.items())
    text = (el.text or "").strip()
    text_s = f" = {text[:60]!r}" if text else ""
    out.append("  " * depth + f"<{el.tag}> {attrs}{text_s}")
    if depth < max_depth:
        for ch in list(el)[:25]:
            out += shape(ch, depth + 1, max_depth)
    return out


def main() -> int:
    xml = Path(sys.argv[1])
    root = ET.fromstring(xml.read_bytes())

    # Index by ObjectID and ObjectUID.
    by_id: dict[str, ET.Element] = {}
    for el in root.iter():
        for a in ("ObjectID", "ObjectUID"):
            if el.get(a):
                by_id[el.get(a)] = el

    def rslv(el, child_tag):
        c = el.find(child_tag) if el is not None else None
        if c is None:
            return None
        for a in ("ObjectRef", "ObjectURef"):
            if c.get(a):
                return by_id.get(c.get(a))
        return c

    # ---- Resolve the MASTER sequence -> trackgroups -> tracks -> items ----
    master = next(
        s for s in root.iter("Sequence")
        if s.get("ObjectUID") and s.findtext("Name") == "2023-03-23 20-59-52"
    )
    print(f"\n##### MASTER sequence map: {master.findtext('Name')} #####")
    tgs = master.find("TrackGroups")
    for tg in tgs.findall("TrackGroup"):
        idx = tg.get("Index")
        cont = None
        sec = tg.find("Second")
        if sec is not None and sec.get("ObjectRef"):
            cont = by_id.get(sec.get("ObjectRef"))
        print(f"\n-- TrackGroup Index={idx} -> {cont.tag if cont is not None else None}")
        if cont is None:
            continue
        if idx == "1":  # video group: dump its internal shape once
            print("   [VideoTrackGroup shape depth 4]")
            print("\n".join("   " + ln for ln in shape(cont, 0, 4)))
        # container -> list of track refs
        tr_holder = None
        for cand in ("Tracks", "TrackGroup"):
            h = cont.find(cand)
            if h is not None:
                tr_holder = h
                break
        if tr_holder is None:
            print("   " + " ".join(shape(cont, 0, 1)))
            continue
        for ti, tref in enumerate(tr_holder.findall("Track")):
            trk = by_id.get(tref.get("ObjectRef")) if tref.get("ObjectRef") else None
            if trk is None:
                continue
            ci = trk.find(".//ClipItems")
            items = []
            if ci is not None:
                for it in ci:
                    res = by_id.get(it.get("ObjectRef")) if it.get("ObjectRef") else it
                    if res is None:
                        continue
                    cti = res.find("ClipTrackItem") or res
                    titem = cti.find("TrackItem") if cti is not None else None
                    st = titem.findtext("Start") if titem is not None else None
                    en = titem.findtext("End") if titem is not None else None
                    scn = None
                    sc = rslv(cti, "SubClip")
                    if sc is not None:
                        scn = sc.findtext("Name")
                    items.append((scn, ssec(titem, "Start") if titem is not None else "-",
                                  ssec(titem, "End") if titem is not None else "-"))
            label = ("V" if idx == "0" else "A") + str(ti + 1)
            if items:
                print(f"  {label}: {len(items)} items")
                for nm, a, b in items[:6]:
                    print(f"     {nm!r}  [{a} -> {b}]")

    # Follow one VideoClipTrackItem fully: trackitem -> subclip -> clip -> media.
    vti = next(e for e in root.iter("VideoClipTrackItem") if len(list(e)))
    cti = vti.find("ClipTrackItem")
    sc_ref = cti.find("SubClip").get("ObjectRef")
    sc = by_id.get(sc_ref)
    print("===== SubClip (resolved) =====")
    print("\n".join(shape(sc, 0, 2)))
    clip_ref = sc.find("Clip").get("ObjectRef") if sc is not None else None
    clip = by_id.get(clip_ref)
    print("\n===== Clip (resolved) — source in/out + media linkage =====")
    print("\n".join(shape(clip, 0, 3)))

    # Sequence -> tracks -> trackitems linkage.
    for tag in ("Sequence", "Track", "VideoClipTrack", "AudioClipTrack", "TrackGroup"):
        nodes = [e for e in root.iter(tag) if len(list(e))]
        if nodes:
            print(f"\n===== {tag}: {len(nodes)}; first shape =====")
            print("\n".join(shape(nodes[0], 0, 2)))

    # How does a master-V7 trackitem reference the GAMEPLAY_NEST sequence?
    print("\n===== nodes naming GAMEPLAY_NEST =====")
    for el in root.iter():
        if (el.text or "").strip() == "GAMEPLAY_NEST":
            par = el.tag
            print(f"  <{par}> text=GAMEPLAY_NEST  (parent chain unknown)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
