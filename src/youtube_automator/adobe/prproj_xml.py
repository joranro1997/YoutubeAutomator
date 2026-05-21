"""Offline .prproj surgeon — read & retime Premiere projects without Premiere.

Premiere 2020 scripting is unusable (public API read-only for geometry; QE
DOM crashes even read-only). So Phase 3 rebuilds the project by editing its
gzipped XML directly. This is deterministic, unit-testable and cannot crash
Premiere.

Data model (reverse-engineered, ticks = 254016000000 / second):

    Sequence(Name)
      TrackGroups → TrackGroup[Index 0=audio,1=video]
        → Second(ObjectRef) → Video/AudioTrackGroup
          → TrackGroup → Tracks → Track[Index] (ObjectURef)
            → Video/AudioClipTrack → ClipTrack → ClipItems
              → *ClipTrackItem*
                  ├─ TrackItem/Start,End          (timeline position)
                  ├─ ComponentOwner/Components ref (EFFECTS — untouched)
                  └─ SubClip → Clip/InPoint,OutPoint (source in/out)

Retiming a clip = rewriting 4 integers (Start/End/InPoint/OutPoint) on
existing nodes. The effect chain (Ultra Keys) rides along untouched because
we never recreate the ClipTrackItem, only its numbers.
"""

from __future__ import annotations

import copy
import gzip
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

TICKS_PER_SEC = 254016000000


def sec_to_ticks(s: float) -> int:
    return int(round(s * TICKS_PER_SEC))


def ticks_to_sec(t: str | int | None) -> float | None:
    if t is None:
        return None
    try:
        return int(t) / TICKS_PER_SEC
    except (TypeError, ValueError):
        return None


@dataclass
class ClipRef:
    """A timeline clip: live ElementTree handles for its 4 timing fields.

    Mutating the seconds properties rewrites the underlying XML text in
    place; Project.save() then reserializes. Effects are NOT touched.
    """

    name: str | None
    track_label: str
    _ti: ET.Element            # TrackItem (has Start/End)
    _clip: ET.Element | None   # Clip (has InPoint/OutPoint); None for nests w/o

    # ---- timeline position ------------------------------------------------ #
    @property
    def start_sec(self) -> float | None:
        return ticks_to_sec(self._ti.findtext("Start"))

    @property
    def end_sec(self) -> float | None:
        return ticks_to_sec(self._ti.findtext("End"))

    def set_timeline(self, start_sec: float, end_sec: float) -> None:
        self._set(self._ti, "Start", sec_to_ticks(start_sec))
        self._set(self._ti, "End", sec_to_ticks(end_sec))

    # ---- source in/out ---------------------------------------------------- #
    @property
    def in_sec(self) -> float | None:
        return ticks_to_sec(self._clip.findtext("InPoint")) if self._clip is not None else None

    @property
    def out_sec(self) -> float | None:
        return ticks_to_sec(self._clip.findtext("OutPoint")) if self._clip is not None else None

    def set_source(self, in_sec: float, out_sec: float) -> None:
        if self._clip is None:
            raise ValueError(f"clip {self.name!r} has no source Clip node")
        self._set(self._clip, "InPoint", sec_to_ticks(in_sec))
        self._set(self._clip, "OutPoint", sec_to_ticks(out_sec))

    @staticmethod
    def _set(parent: ET.Element, tag: str, value: int) -> None:
        el = parent.find(tag)
        if el is None:
            raise KeyError(f"<{tag}> not found under <{parent.tag}>")
        el.text = str(value)


class Project:
    """A loaded .prproj (gzipped XML) with dual ObjectID/ObjectUID indexing."""

    def __init__(self, root: ET.Element):
        self.root = root
        self._by_id: dict[str, ET.Element] = {}
        for el in root.iter():
            for a in ("ObjectID", "ObjectUID"):
                v = el.get(a)
                if v:
                    self._by_id[v] = el
        self._parent: dict[ET.Element, ET.Element] = {}
        for parent in root.iter():
            for ch in parent:
                self._parent[ch] = parent
        ids = [int(v) for v in self._by_id if v.isdigit()]
        self._next_id = (max(ids) + 1) if ids else 1

    # ---- node id allocation ---------------------------------------------- #
    def _alloc_id(self) -> str:
        v = str(self._next_id)
        self._next_id += 1
        return v

    def _reindex(self, el: ET.Element) -> None:
        for a in ("ObjectID", "ObjectUID"):
            if el.get(a):
                self._by_id[el.get(a)] = el

    # ---- load / save ------------------------------------------------------ #
    @classmethod
    def load(cls, path: Path) -> "Project":
        raw = Path(path).read_bytes()
        data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        return cls(ET.fromstring(data))

    def save(self, path: Path) -> Path:
        xml_bytes = ET.tostring(self.root, encoding="UTF-8", xml_declaration=True)
        Path(path).write_bytes(gzip.compress(xml_bytes))
        return Path(path)

    # ---- resolution ------------------------------------------------------- #
    def _resolve(self, el: ET.Element | None, child_tag: str) -> ET.Element | None:
        if el is None:
            return None
        c = el.find(child_tag)
        if c is None:
            return None
        for a in ("ObjectRef", "ObjectURef"):
            if c.get(a):
                return self._by_id.get(c.get(a))
        return c

    def _deref(self, el: ET.Element) -> ET.Element:
        """If el carries a ref attr, follow it; else return el itself."""
        for a in ("ObjectRef", "ObjectURef"):
            if el.get(a):
                return self._by_id.get(el.get(a), el)
        return el

    # ---- traversal -------------------------------------------------------- #
    def sequence(self, name: str) -> ET.Element:
        for s in self.root.iter("Sequence"):
            if s.get("ObjectUID") and s.findtext("Name") == name:
                return s
        raise KeyError(f"sequence {name!r} not found")

    def tracks(self, seq: ET.Element, kind: str) -> list[tuple[str, ET.Element]]:
        """Ordered [(label, clipTrack_element), ...] for kind 'video'|'audio'."""
        want_index = "1" if kind == "video" else "0"
        prefix = "V" if kind == "video" else "A"
        tgs = seq.find("TrackGroups")
        cont = None
        for tg in tgs.findall("TrackGroup"):
            if tg.get("Index") == want_index:
                sec = tg.find("Second")
                if sec is not None and sec.get("ObjectRef"):
                    cont = self._by_id.get(sec.get("ObjectRef"))
                break
        if cont is None:
            return []
        holder = cont.find("TrackGroup")
        tracks_el = holder.find("Tracks") if holder is not None else None
        if tracks_el is None:
            return []
        out: list[tuple[str, ET.Element]] = []
        for tref in tracks_el.findall("Track"):
            ct = self._deref(tref)
            idx = tref.get("Index")
            out.append((f"{prefix}{int(idx) + 1}", ct))
        return out

    def clips(self, clip_track: ET.Element, label: str) -> list[ClipRef]:
        """Resolved ClipRefs for a Video/AudioClipTrack, ordered by timeline.

        Structure: ClipTrack → ClipItems → TrackItems → TrackItem[Index]
        (ObjectRef) → *ClipTrackItem* → TrackItem(Start/End) + SubClip.
        """
        ci = clip_track.find(".//ClipItems")
        tis = ci.find("TrackItems") if ci is not None else None
        if tis is None:
            return []
        entries = sorted(
            tis.findall("TrackItem"),
            key=lambda e: int(e.get("Index", "0")),
        )
        out: list[ClipRef] = []
        for entry in entries:
            res = self._deref(entry)              # -> Video/AudioClipTrackItem
            if res is None:
                continue
            cti = res.find("ClipTrackItem")
            if cti is None and res.tag == "ClipTrackItem":
                cti = res
            if cti is None:
                continue
            ti = cti.find("TrackItem")            # the Start/End holder
            if ti is None or ti.find("Start") is None:
                continue
            sub = self._resolve(cti, "SubClip")
            name = sub.findtext("Name") if sub is not None else None
            clip_node = self._resolve(sub, "Clip") if sub is not None else None
            # Clip wrapper -> inner <Clip Version=...> holds InPoint/OutPoint.
            inner = clip_node.find("Clip") if clip_node is not None else None
            src = (
                inner
                if (inner is not None and inner.find("InPoint") is not None)
                else clip_node
            )
            out.append(ClipRef(name=name, track_label=label, _ti=ti, _clip=src))
        return out

    # ---- clip cloning (M2) ----------------------------------------------- #
    def _clone_into_doc(self, orig: ET.Element, idmap: dict[str, str]) -> ET.Element:
        """Deep-copy `orig`, give it a fresh ObjectID, append beside the
        original, index it. Records old->new in idmap.
        """
        c = copy.deepcopy(orig)
        oid = orig.get("ObjectID")
        if oid:
            nid = self._alloc_id()
            c.set("ObjectID", nid)
            idmap[oid] = nid
        par = self._parent.get(orig, self.root)
        par.append(c)
        self._parent[c] = par
        for e in c.iter():
            self._reindex(e)
        return c

    def clone_clip(self, vti: ET.Element) -> tuple["ClipRef", ET.Element]:
        """Clone the per-instance node set of a timeline clip:
        {VideoClipTrackItem, its Components, SubClip, VideoClip, Markers}.
        Shared media (VideoMediaSource/MasterClip/Media) is kept by ref.
        Returns a ClipRef onto the NEW clip (caller sets timeline/source).
        """
        cti = vti.find("ClipTrackItem") or vti
        comp_ref = cti.find("ComponentOwner/Components")
        sub = self._resolve(cti, "SubClip")
        vclip = self._resolve(sub, "Clip") if sub is not None else None
        inner = vclip.find("Clip") if vclip is not None else None
        comp = (
            self._by_id.get(comp_ref.get("ObjectRef"))
            if comp_ref is not None and comp_ref.get("ObjectRef")
            else None
        )
        mk_el = inner.find("MarkerOwner/Markers") if inner is not None else None
        mk = (
            self._by_id.get(mk_el.get("ObjectRef"))
            if mk_el is not None and mk_el.get("ObjectRef")
            else None
        )

        idmap: dict[str, str] = {}
        new_vti = self._clone_into_doc(vti, idmap)
        new_sub = self._clone_into_doc(sub, idmap) if sub is not None else None
        new_vclip = self._clone_into_doc(vclip, idmap) if vclip is not None else None
        if comp is not None:
            self._clone_into_doc(comp, idmap)
        if mk is not None:
            self._clone_into_doc(mk, idmap)

        # Repoint internal refs (per-instance) to the clones; leave refs to
        # shared media (not in idmap) and all ObjectURef untouched.
        for node in (new_vti, new_sub, new_vclip):
            if node is None:
                continue
            for e in node.iter():
                r = e.get("ObjectRef")
                if r and r in idmap:
                    e.set("ObjectRef", idmap[r])

        ti = new_vti.find("ClipTrackItem/TrackItem")
        new_inner = new_vclip.find("Clip") if new_vclip is not None else None
        src = (
            new_inner
            if (new_inner is not None and new_inner.find("InPoint") is not None)
            else new_vclip
        )
        name = new_sub.findtext("Name") if new_sub is not None else None
        return ClipRef(name=name, track_label="?", _ti=ti, _clip=src), new_vti

    def _track_items_holder(self, clip_track: ET.Element) -> ET.Element | None:
        ci = clip_track.find(".//ClipItems")
        return ci.find("TrackItems") if ci is not None else None

    def clear_track(self, clip_track: ET.Element) -> None:
        """Remove every <TrackItem> pointer from a track (orphan nodes are
        ignored by Premiere). The track becomes empty."""
        tis = self._track_items_holder(clip_track)
        if tis is None:
            return
        for e in list(tis.findall("TrackItem")):
            tis.remove(e)

    def add_clip(self, clip_track: ET.Element, new_vti: ET.Element) -> None:
        """Register a (cloned) VideoClipTrackItem on a track, next index."""
        tis = self._track_items_holder(clip_track)
        if tis is None:
            raise ValueError("track has no TrackItems holder")
        idx = len(tis.findall("TrackItem"))
        e = ET.SubElement(tis, "TrackItem")
        e.set("Index", str(idx))
        e.set("ObjectRef", new_vti.get("ObjectID"))

    # ---- M2b: new-media injection (clone a media cluster + repath) ------- #
    MEDIA_TAGS = {
        "ClipProjectItem", "MasterClip", "LoggingInfo", "AudioComponentChains",
        "AudioComponentChain", "AudioClipChannelGroups", "AudioClipChannelGroup",
        "VideoClip", "AudioClip", "VideoMediaSource", "AudioMediaSource",
        "Media", "VideoStream", "AudioStream", "MarkerOwner", "Markers",
    }

    def _closure(self, start: ET.Element, allowed: set[str]) -> list[ET.Element]:
        """BFS following ObjectRef/ObjectURef, only into `allowed` tags.
        Refs to other tags are left pointing at the shared originals."""
        seen: dict[int, ET.Element] = {id(start): start}
        order: list[ET.Element] = [start]
        queue = [start]
        while queue:
            node = queue.pop(0)
            for el in node.iter():
                for a in ("ObjectRef", "ObjectURef"):
                    ref = el.get(a)
                    if not ref:
                        continue
                    tgt = self._by_id.get(ref)
                    if tgt is None or tgt.tag not in allowed:
                        continue
                    if id(tgt) in seen:
                        continue
                    seen[id(tgt)] = tgt
                    order.append(tgt)
                    queue.append(tgt)
        return order

    def _find_bin_item(self, name: str) -> ET.Element | None:
        for cpi in self.root.iter("ClipProjectItem"):
            pi = cpi.find("ProjectItem")
            if pi is not None and (pi.findtext("Name") or "") == name:
                return cpi
        return None

    def clone_media_cluster(
        self, blueprint_name: str, new_path: Path | str, duration_sec: float
    ) -> dict:
        """Clone the full media cluster of an imported file, repath it to
        `new_path` and set its duration. Returns the new media handles so a
        cloned trackitem can be repointed at it.
        """
        cpi = self._find_bin_item(blueprint_name)
        if cpi is None:
            raise KeyError(f"no bin item named {blueprint_name!r} to clone")
        cluster = self._closure(cpi, self.MEDIA_TAGS)

        idmap: dict[str, str] = {}
        clones: list[ET.Element] = []
        for orig in cluster:
            c = copy.deepcopy(orig)
            if orig.get("ObjectID"):
                nid = self._alloc_id()
                idmap[orig.get("ObjectID")] = nid
                c.set("ObjectID", nid)
            if orig.get("ObjectUID"):
                nu = str(uuid.uuid4())
                idmap[orig.get("ObjectUID")] = nu
                c.set("ObjectUID", nu)
            clones.append(c)

        # Remap internal refs (to nodes we cloned); leave shared refs alone.
        for c in clones:
            for el in c.iter():
                for a in ("ObjectRef", "ObjectURef"):
                    r = el.get(a)
                    if r and r in idmap:
                        el.set(a, idmap[r])

        for c in clones:
            self.root.append(c)
            self._parent[c] = self.root
            for e in c.iter():
                self._reindex(e)

        def clone_of(tag: str) -> ET.Element | None:
            return next((c for c in clones if c.tag == tag), None)

        new_cpi = clone_of("ClipProjectItem")
        new_mc = clone_of("MasterClip")
        new_media = clone_of("Media")
        new_vms = clone_of("VideoMediaSource")
        new_ams = clone_of("AudioMediaSource")

        np = str(Path(new_path))
        nm = Path(new_path).name

        def set_text(parent: ET.Element | None, tag: str, val: str) -> None:
            if parent is None:
                return
            e = parent.find(tag)
            if e is not None:
                e.text = val

        for tag in ("FilePath", "ActualMediaFilePath", "RelativePath", "Title"):
            set_text(new_media, tag, np if tag != "Title" else nm)
        if new_cpi is not None:
            set_text(new_cpi.find("ProjectItem"), "Name", nm)
        set_text(new_mc, "Name", nm)

        ticks = str(sec_to_ticks(duration_sec))
        set_text(new_vms, "OriginalDuration", ticks)
        set_text(new_ams, "OriginalDuration", ticks)
        if new_media is not None:
            for stag in ("VideoStream", "AudioStream"):
                sref = new_media.find(stag)
                snode = (
                    self._by_id.get(sref.get("ObjectRef"))
                    if sref is not None and sref.get("ObjectRef")
                    else None
                )
                set_text(snode, "Duration", ticks)

        return {
            "master_uid": new_mc.get("ObjectUID") if new_mc is not None else None,
            "vms_id": new_vms.get("ObjectID") if new_vms is not None else None,
            "ams_id": new_ams.get("ObjectID") if new_ams is not None else None,
            "name": nm,
        }

    def repoint_clip_media(self, vti: ET.Element, media: dict) -> None:
        """Make a (cloned) trackitem reference an injected media cluster:
        SubClip→MasterClip and the Clip's Source are redirected."""
        cti = vti.find("ClipTrackItem") or vti
        sub = self._resolve(cti, "SubClip")
        if sub is None:
            return
        mref = sub.find("MasterClip")
        if mref is not None and media.get("master_uid"):
            mref.set("ObjectURef", media["master_uid"])
        vclip = self._resolve(sub, "Clip")
        inner = vclip.find("Clip") if vclip is not None else None
        src = inner.find("Source") if inner is not None else None
        if src is not None:
            is_audio = vti.tag == "AudioClipTrackItem"
            new_src = media.get("ams_id") if is_audio else media.get("vms_id")
            if new_src:
                src.set("ObjectRef", new_src)

    def find_clip_template(
        self, filename_contains: str, kind: str = "video"
    ) -> ET.Element | None:
        """A Video/AudioClipTrackItem whose SubClip Name matches — used as the
        clone source so the whole media-linkage chain comes for free."""
        tag = "VideoClipTrackItem" if kind == "video" else "AudioClipTrackItem"
        for vti in self.root.iter(tag):
            cti = vti.find("ClipTrackItem")
            if cti is None:
                continue
            sub = self._resolve(cti, "SubClip")
            nm = sub.findtext("Name") if sub is not None else ""
            if nm and filename_contains.lower() in nm.lower():
                return vti
        return None

    def tracks_by_label(self, seq_name: str) -> dict[str, ET.Element]:
        seq = self.sequence(seq_name)
        out: dict[str, ET.Element] = {}
        for kind in ("video", "audio"):
            for label, ct in self.tracks(seq, kind):
                out[label] = ct
        return out

    # ---- convenience ------------------------------------------------------ #
    def map_sequence(self, name: str) -> dict[str, list[ClipRef]]:
        seq = self.sequence(name)
        result: dict[str, list[ClipRef]] = {}
        for kind in ("video", "audio"):
            for label, ct in self.tracks(seq, kind):
                cl = self.clips(ct, label)
                if cl:
                    result[label] = cl
        return result
