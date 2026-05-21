"""Reverse-engineer the media-node cluster of an imported recording so M2b
can clone+repath it for brand-new files."""
import xml.etree.ElementTree as ET
from pathlib import Path

from youtube_automator.adobe.prproj_xml import Project

p = Project.load(Path("assets/premiere_templates/lom_nest.prproj"))
root, by_id = p.root, p._by_id

NAME = "2026-05-04 19-24-23.mp4"


def short(el, n=500):
    return ET.tostring(el, encoding="unicode")[:n]


# 1) MasterClip by Name
mc = None
for m in root.iter("MasterClip"):
    if (m.findtext("Name") or "") == NAME:
        mc = m
        break
print("MasterClip:", mc.tag, "UID=", mc.get("ObjectUID"))
print("children:", [c.tag for c in mc])
print(short(mc, 900))

# 2) Any node carrying the on-disk path (search whole doc for the filename)
print("\n== nodes whose text contains the file path ==")
seen = set()
for el in root.iter():
    t = (el.text or "")
    if "19-24-23" in t and el.tag not in seen:
        seen.add(el.tag)
        print(f"<{el.tag}> = {t[:160]}")

# 3) The project bin ProjectItem that references this MasterClip
print("\n== ProjectItem / bin entries referencing the MasterClip ==")
uid = mc.get("ObjectUID")
cnt = 0
for el in root.iter():
    for a in ("ObjectRef", "ObjectURef"):
        if el.get(a) == uid:
            par = p._parent.get(el)
            print(f"<{el.tag} {a}={uid}> parent=<{par.tag if par is not None else '?'}>")
            cnt += 1
print("ref count to MasterClip:", cnt)

# 4) Media node (via a VideoMediaSource->Media) + its children/path/duration
print("\n== VideoMediaSource / Media for this file ==")
for vms in root.iter("VideoMediaSource"):
    od = vms.findtext("OriginalDuration")
    media_ref = vms.find("MediaSource/Media")
    if media_ref is None:
        continue
    media = by_id.get(media_ref.get("ObjectURef"))
    if media is not None and (media.findtext("FilePath") or "" ).find("19-24-23") >= 0 \
       or (media is not None and "19-24-23" in ET.tostring(media, encoding="unicode")):
        print("VideoMediaSource ObjectID=", vms.get("ObjectID"), "OriginalDuration=", od)
        print("Media:", media.tag, "UID=", media.get("ObjectUID"),
              "children=", [c.tag for c in media])
        print(short(media, 1200))
        break
