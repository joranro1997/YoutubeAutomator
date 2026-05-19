"""Nail the exact node set to clone for M2b media injection:
ClipProjectItem(bin) + MasterClip + Video/AudioMediaSource + Media + streams.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from youtube_automator.adobe.prproj_xml import Project

p = Project.load(Path("assets/premiere_templates/lom_nest.prproj"))
root, by_id = p.root, p._by_id
NAME = "2026-05-04 19-24-23.mp4"


def sh(el, n=700):
    return ET.tostring(el, encoding="unicode")[:n] if el is not None else "None"


# bin ClipProjectItem -> MasterClip
mc = next(m for m in root.iter("MasterClip") if (m.findtext("Name") or "") == NAME)
uid = mc.get("ObjectUID")
cpi = None
for el in root.iter("ClipProjectItem"):
    r = el.find("MasterClip")
    if r is not None and r.get("ObjectURef") == uid:
        cpi = el
        break
print("== ClipProjectItem (bin entry) ==")
print("children:", [c.tag for c in cpi] if cpi is not None else None)
print(sh(cpi, 900))
par = p._parent.get(cpi)
print("parent of ClipProjectItem:", par.tag if par is not None else None,
      par.get("ObjectID") if par is not None else None)

# MasterClip.Clips -> what do they resolve to?
print("\n== MasterClip.Clips refs ==")
for c in mc.find("Clips"):
    tgt = by_id.get(c.get("ObjectRef"))
    print(f"  Clip Index={c.get('Index')} -> <{tgt.tag if tgt is not None else '?'}> "
          f"id={tgt.get('ObjectID') if tgt is not None else '?'}")

# AudioMediaSource for this file
print("\n== AudioMediaSource ==")
for ams in root.iter("AudioMediaSource"):
    s = ET.tostring(ams, encoding="unicode")
    if "19-24-23" in s or (ams.find("MediaSource/Media") is not None):
        med = ams.find("MediaSource/Media")
        media = by_id.get(med.get("ObjectURef")) if med is not None else None
        if media is not None and "19-24-23" in ET.tostring(media, encoding="unicode"):
            print("AudioMediaSource id=", ams.get("ObjectID"), sh(ams, 500))
            break

# Streams of the Media
print("\n== Media streams ==")
media = None
for vms in root.iter("VideoMediaSource"):
    mref = vms.find("MediaSource/Media")
    m = by_id.get(mref.get("ObjectURef")) if mref is not None else None
    if m is not None and "19-24-23" in ET.tostring(m, encoding="unicode"):
        media = m
        break
for tag in ("VideoStream", "AudioStream"):
    r = media.find(tag)
    node = by_id.get(r.get("ObjectRef")) if r is not None and r.get("ObjectRef") else None
    print(f"-- {tag} id={node.get('ObjectID') if node is not None else '?'} --")
    print(sh(node, 700))

# Count duration-bearing fields anywhere referencing this media's duration
print("\n== duration-ish fields in VideoMediaSource ==")
for vms in root.iter("VideoMediaSource"):
    mref = vms.find("MediaSource/Media")
    m = by_id.get(mref.get("ObjectURef")) if mref is not None else None
    if m is media:
        for ch in vms.iter():
            if "uration" in ch.tag or ch.tag in ("Start", "End"):
                print(f"  <{ch.tag}> = {ch.text}")
        break
