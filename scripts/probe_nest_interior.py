"""Map GAMEPLAY_NEST interior + dump the full node closure of one gameplay
clip (so we know exactly what to clone/repath for M2 media injection)."""
import xml.etree.ElementTree as ET
from pathlib import Path

from youtube_automator.adobe.prproj_xml import Project

p = Project.load(Path("assets/premiere_templates/lom_nest.prproj"))

print("== GAMEPLAY_NEST interior map ==")
try:
    m = p.map_sequence("GAMEPLAY_NEST")
    for lbl in sorted(m, key=lambda x: (x[0], int(x[1:]))):
        cs = m[lbl]
        print(lbl, len(cs), [(c.name, round(c.start_sec or 0, 1), round(c.end_sec or 0, 1),
                              round(c.in_sec, 1) if c.in_sec is not None else None) for c in cs[:6]])
except Exception as e:
    print("map error:", e)

# Dump the reference closure of ONE gameplay trackitem from the nest interior.
root = p.root
by_id = p._by_id

def find_first_gameplay_vti():
    for vti in root.iter("VideoClipTrackItem"):
        cti = vti.find("ClipTrackItem")
        if cti is None:
            continue
        sub = p._resolve(cti, "SubClip")
        if sub is not None and "19-24-23" in str(sub.findtext("Name") or ""):
            return vti, cti, sub
    return None, None, None

vti, cti, sub = find_first_gameplay_vti()
print("\n== gameplay VideoClipTrackItem closure ==")
print("VideoClipTrackItem ObjectID=", vti.get("ObjectID"))
print("SubClip:", ET.tostring(sub, encoding="unicode")[:400])
clip = p._resolve(sub, "Clip")
print("\nClip wrapper tag:", clip.tag, "ObjectID=", clip.get("ObjectID"))
inner = clip.find("Clip")
print("inner Clip children:", [c.tag for c in inner] if inner is not None else None)
src = p._resolve(inner, "Source") if inner is not None else None
print("\nSource node:", src.tag if src is not None else None,
      "ObjectID=", src.get("ObjectID") if src is not None else None)
if src is not None:
    print(ET.tostring(src, encoding="unicode")[:600])
# MasterClip via SubClip
mc_ref = sub.find("MasterClip")
mc = by_id.get(mc_ref.get("ObjectURef")) if mc_ref is not None else None
print("\nMasterClip:", mc.tag if mc is not None else None,
      "UID=", mc.get("ObjectUID") if mc is not None else None)
if mc is not None:
    print("MasterClip children:", [c.tag for c in mc][:20])
    # find media path
    for el in mc.iter():
        if el.tag in ("ActualMediaFilePath", "FilePath", "Path") and (el.text or "").strip():
            print("  media path node <%s> = %s" % (el.tag, el.text))
