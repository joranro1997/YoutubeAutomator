"""Inspect how a VideoClipTrack links to its trackitems (ClipItems entries)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = ET.fromstring(Path("data/tmp/lom_nest.xml").read_bytes())
by_id = {}
for el in root.iter():
    for a in ("ObjectID", "ObjectUID"):
        if el.get(a):
            by_id[el.get(a)] = el


def deref(el):
    for a in ("ObjectRef", "ObjectURef"):
        if el.get(a):
            return by_id.get(el.get(a), el)
    return el


def shape(el, d=0, md=3):
    out = []
    a = " ".join(f"{k}={v!r}" for k, v in el.attrib.items())
    t = (el.text or "").strip()
    out.append("  " * d + f"<{el.tag}> {a}" + (f" = {t[:50]!r}" if t else ""))
    if d < md:
        for c in list(el)[:14]:
            out += shape(c, d + 1, md)
    return out


# Master seq -> video group -> Tracks -> first Track (ObjectURef) -> resolve
seq = next(s for s in root.iter("Sequence")
           if s.findtext("Name") == "2023-03-23 20-59-52" and s.get("ObjectUID"))
tg = [t for t in seq.find("TrackGroups").findall("TrackGroup") if t.get("Index") == "1"][0]
vtg = by_id[tg.find("Second").get("ObjectRef")]
tracks = vtg.find("TrackGroup").find("Tracks").findall("Track")
print(f"video Tracks entries: {len(tracks)}")
print("first Track entry attrs:", tracks[0].attrib)
ct = deref(tracks[0])
print("resolved ->", ct.tag, ct.attrib)
print("\n[resolved track shape depth 4]")
print("\n".join(shape(ct, 0, 4)))

# Find a Track that actually has clip items (e.g. V7 = index 6)
print("\n[track index 6 (V7) resolved shape]")
ct7 = deref(tracks[6])
print("\n".join(shape(ct7, 0, 5)))
