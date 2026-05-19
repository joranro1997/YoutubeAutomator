"""Premiere automation via ExtendScript generation (public API, Premiere 14+).

Design (from the Phase 3 spike):
  * Public ExtendScript API drives all clip placement (importFiles,
    track.overwriteClip) — confirmed working live on 14.3.1.
  * The 3 tuned Ultra Keys live permanently on the GAMEPLAY_NEST clip on
    master V7 (the *_nest.prproj migration). The rebuild only swaps the
    *contents* of the nested sequence and never recreates effects.
  * ALL timeline math is done here in Python (deterministic, unit-tested)
    and emitted as explicit placement lists. The generated .jsx is a dumb
    executor: clear tracks, place the lists, stretch decor/music, recompute
    the promo-anchored overlays by simple rules. Heavy logging; supervised
    first run (no Media Encoder).

Windows-only at runtime; run the .jsx via the ExtendScript Debugger.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import GameConfig
from ..paths import OUTPUTS_DIR, TMP_DIR, premiere_templates_dir
from .edit_plan import EditPlan

EPS = 0.05  # seconds; float tolerance for overlay phase classification.


def track_index(label: str) -> int:
    """'V7'/'A1' (1-based UI label) -> 0-based ExtendScript track index."""
    return int(label[1:]) - 1


def _js(obj) -> str:
    return json.dumps(obj)


# --------------------------------------------------------------------------- #
# Timeline math (pure, testable) -> explicit placement lists
# --------------------------------------------------------------------------- #
def _gameplay_pieces(plan: EditPlan) -> list[dict]:
    """Concatenate every keep-segment into one gameplay timeline.

    Returns pieces with cumulative interior offsets (0 .. gameplay_dur):
      {src, src_in, src_out, g_start, g_end}
    """
    pieces: list[dict] = []
    g = 0.0
    for fr in plan.fragments:
        for k in fr.keep_segments:
            dur = round(k.src_out_sec - k.src_in_sec, 4)
            pieces.append(
                {
                    "src": fr.path,
                    "src_in": k.src_in_sec,
                    "src_out": k.src_out_sec,
                    "g_start": round(g, 4),
                    "g_end": round(g + dur, 4),
                }
            )
            g = round(g + dur, 4)
    return pieces


def _master_audio_pieces(plan: EditPlan, pieces: list[dict]) -> list[dict]:
    """Map gameplay pieces to MASTER time, opening a hole for the promo.

    A piece straddling the insertion point is split; the post-promo part is
    shifted by the promo block duration so audio stays in sync with the
    nest video shown on master V7.
    """
    if not plan.promo.present:
        return [
            {"src": p["src"], "src_in": p["src_in"], "src_out": p["src_out"],
             "at": p["g_start"]}
            for p in pieces
        ]
    at_cut = plan.promo_insertion_sec
    block = plan.promo.block_duration_sec
    out: list[dict] = []
    for p in pieces:
        gs, ge = p["g_start"], p["g_end"]
        if ge <= at_cut + 1e-6:
            out.append({"src": p["src"], "src_in": p["src_in"],
                        "src_out": p["src_out"], "at": gs})
        elif gs >= at_cut - 1e-6:
            out.append({"src": p["src"], "src_in": p["src_in"],
                        "src_out": p["src_out"], "at": round(gs + block, 4)})
        else:  # straddles the cut -> split
            src_mid = round(p["src_in"] + (at_cut - gs), 4)
            out.append({"src": p["src"], "src_in": p["src_in"],
                        "src_out": src_mid, "at": gs})
            out.append({"src": p["src"], "src_in": src_mid,
                        "src_out": p["src_out"], "at": round(at_cut + block, 4)})
    return out


def _nest_master_clips(plan: EditPlan) -> list[dict]:
    """How GAMEPLAY_NEST sits on master V7 (1 piece, or 2 around the promo).

    in/out are interior (nest) times; at is the master time.
    """
    g = plan.gameplay_duration_sec
    if not plan.promo.present:
        return [{"in": 0.0, "out": g, "at": 0.0}]
    at_cut = plan.promo_insertion_sec
    block = plan.promo.block_duration_sec
    return [
        {"in": 0.0, "out": at_cut, "at": 0.0},
        {"in": at_cut, "out": g, "at": round(at_cut + block, 4)},
    ]


def _promo_master_pieces(plan: EditPlan, role: str) -> list[dict]:
    """Promo subclips of `role` placed on master, relative to the insertion."""
    base = plan.promo_insertion_sec
    out: list[dict] = []
    for s in plan.promo.subclips:
        if s.track_role != role:
            continue
        out.append(
            {
                "src_in": s.src_in_sec,
                "src_out": s.src_out_sec,
                "at": round(base + s.rel_start_sec, 4),
            }
        )
    return out


def compute_layout(plan: EditPlan) -> dict:
    """Everything the .jsx needs as explicit instructions."""
    pf = plan.template_profile
    pieces = _gameplay_pieces(plan)
    return {
        "sequenceName": pf.get("sequence_name", ""),
        "nestName": "GAMEPLAY_NEST",
        "contentV": track_index(pf["content_video_track"]),
        "gameplayA": track_index(pf["gameplay_audio_track"]),
        "musicA": track_index(pf["music_track"]),
        "decorV": [track_index(x) for x in pf.get("static_decor_video_tracks", [])],
        "overlayV": [track_index(x) for x in pf.get("overlay_tracks", [])],
        "total": plan.total_duration_sec,
        "tplTotal": plan.tpl_total_sec,
        "promoPresent": plan.promo.present,
        "promoAsset": plan.promo.asset_path,
        "promoInsertion": plan.promo_insertion_sec,
        "promoBlock": plan.promo.block_duration_sec,
        "tplPromoStart": plan.promo.tpl_start_sec,
        "tplPromoEnd": plan.promo.tpl_end_sec,
        "eps": EPS,
        # explicit placement lists
        "nestVideo": [
            {"src": p["src"], "in": p["src_in"], "out": p["src_out"], "at": p["g_start"]}
            for p in pieces
        ],
        "nestMasterClips": _nest_master_clips(plan),
        "masterAudio": _master_audio_pieces(plan, pieces),
        "promoVideo": _promo_master_pieces(plan, "content_video"),
        "promoAudio": _promo_master_pieces(plan, "gameplay_audio"),
    }


# --------------------------------------------------------------------------- #
# Shared ExtendScript helper library (ES3-safe)
# --------------------------------------------------------------------------- #
ESX_LIB = r"""
var LOG = [];
function log(m){ LOG.push(String(m)); }
function dumpLog(p){ var f=new File(p); f.encoding="UTF-8"; f.open("w"); f.write(LOG.join("\n")); f.close(); }
function safe(fn,fb){ try{ var r=fn(); return r===undefined?fb:r; }catch(e){ log("  safe: "+e); return fb; } }
function findByPath(item,want){
  var w=String(want).toLowerCase().replace(/\\/g,"/");
  try{ for(var i=0;i<item.children.numItems;i++){ var c=item.children[i];
    if(c.type===ProjectItemType.BIN){ var d=findByPath(c,want); if(d) return d; }
    else { var mp=safe(function(){return c.getMediaPath();},"");
      if(mp&&String(mp).toLowerCase().replace(/\\/g,"/")===w) return c; } } }catch(e){}
  return null;
}
function findByName(item,want){
  try{ for(var i=0;i<item.children.numItems;i++){ var c=item.children[i];
    if(String(c.name)===String(want)) return c;
    if(c.type===ProjectItemType.BIN){ var d=findByName(c,want); if(d) return d; } } }catch(e){}
  return null;
}
function ensureItem(path){
  var hit=findByPath(app.project.rootItem,path);
  if(hit) return hit;
  app.project.importFiles([path],true,app.project.rootItem,false);
  return findByPath(app.project.rootItem,path);
}
function seqByName(n){
  for(var i=0;i<app.project.sequences.numSequences;i++){
    var s=app.project.sequences[i]; if(String(s.name)===String(n)) return s; }
  return null;
}
function clearVTrack(seq,idx){ var t=seq.videoTracks[idx];
  while(t.clips.numItems>0){ t.clips[0].remove(false,false); } }
function clearATrack(seq,idx){ var t=seq.audioTracks[idx];
  while(t.clips.numItems>0){ t.clips[0].remove(false,false); } }
/* Place src sub-range [inS,outS] of projectItem at atS on a track. */
function placeRange(track,item,atS,inS,outS){
  safe(function(){ item.setInPoint(inS); });
  safe(function(){ item.setOutPoint(outS); });
  track.overwriteClip(item,atS);
  return track.clips[track.clips.numItems-1];
}
function setSpan(clip,startS,endS){
  /* extend/contract a clip to [startS,endS]; tolerant of API quirks */
  safe(function(){ clip.end=endS; });
  safe(function(){ clip.start=startS; });
}
/* Collapse a track to a single clip: keep clips[0] (its effects/settings),
   remove any extras (e.g. collateral Ctrl+K splits during migration). */
function consolidateTrack(track){
  while(track.clips.numItems>1){
    track.clips[track.clips.numItems-1].remove(false,false);
  }
  return track.clips.numItems>0 ? track.clips[0] : null;
}
/* Re-fit an EXISTING (effect-bearing) clip without recreating it:
   show source/interior [inS,outS] and sit at master time atS.
   Order matters: set in/out first, then move into place, then trim end. */
function fitClip(clip,inS,outS,atS){
  safe(function(){ clip.inPoint=inS; });
  safe(function(){ clip.outPoint=outS; });
  var cur=safe(function(){ return clip.start.seconds; },0);
  var dt=atS-cur;
  if(Math.abs(dt)>0.0005){
    var moved=safe(function(){ clip.move(dt); return true; },false);
    if(!moved){ safe(function(){ clip.start=atS; }); }
  }
  safe(function(){ clip.end=atS+(outS-inS); });
}
"""


# --------------------------------------------------------------------------- #
# 1) One-time template -> nest migration (kept for reference / re-runs)
# --------------------------------------------------------------------------- #
def generate_migration_jsx(game: GameConfig) -> Path:
    """Legacy helper — the migration is now a guided manual checklist
    (native Nest + Paste Attributes). Kept so it can be regenerated if the
    template is ever rebuilt from scratch.
    """
    pt = game.premiere_template
    seq_name = pt.sequence_name
    content_idx = track_index(pt.content_video_track)
    log_path = (TMP_DIR / f"{game.slug}_migration.log").as_posix()
    jsx = f"""/* one-time nest migration helper for {game.slug} (manual checklist preferred) */
{ESX_LIB}
(function(){{
  var LOGP={_js(log_path)};
  var seq={_js(seq_name)}?seqByName({_js(seq_name)}):app.project.activeSequence;
  if(!seq){{ alert("sequence not found"); return; }}
  app.project.activeSequence=seq;
  log("Use the manual checklist: select V{content_idx+1} clips -> Nest -> "
    + "Paste Attributes -> Save As {game.slug}_nest.prproj");
  dumpLog(LOGP);
  alert("See the guided checklist in chat — manual Nest is more reliable.");
}})();
"""
    out = TMP_DIR / f"{game.slug}_migration.jsx"
    out.write_text(jsx, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# 2) Per-video rebuild
# --------------------------------------------------------------------------- #
def generate_rebuild_jsx(plan: EditPlan, output_jsx: Path | None = None) -> Path:
    """Write the per-video rebuild .jsx from an EditPlan.

    Supervised mode: builds the timeline and leaves the project open; it
    does NOT queue Media Encoder. Run it via the ExtendScript Debugger,
    then eyeball + export.
    """
    L = compute_layout(plan)
    log_path = (TMP_DIR / f"{plan.game_slug}_{plan.video_slug}_rebuild.log").as_posix()
    # Edits happen on a per-video COPY so the *_nest.prproj template is never
    # mutated. The .jsx saveAs's here before touching a single clip.
    proj_copy = (
        OUTPUTS_DIR / plan.game_slug / plan.video_slug / f"{plan.video_slug}.prproj"
    )
    proj_copy.parent.mkdir(parents=True, exist_ok=True)
    proj_copy_posix = proj_copy.as_posix()

    jsx = f"""/* PER-VIDEO REBUILD — generated for {plan.game_slug}/{plan.video_slug}.
   Supervised: builds the timeline, does NOT render. Do not hand-edit. */
{ESX_LIB}
(function(){{
  var LOGP={_js(log_path)};
  var P={_js(L)};
  log("=== rebuild {plan.game_slug}/{plan.video_slug} ===");

  // Safety: save the open *_nest.prproj AS a per-video copy before editing.
  var COPY={_js(proj_copy_posix)};
  var saved=safe(function(){{ app.project.saveAs(COPY); return true; }},false);
  if(!saved){{ dumpLog(LOGP);
    alert("Could not Save As the per-video copy:\\n"+COPY
      +"\\n\\nAborting so the template stays clean."); return; }}
  log("saved working copy: "+COPY);

  var master = P.sequenceName ? seqByName(P.sequenceName) : app.project.activeSequence;
  if(!master){{ dumpLog(LOGP); alert("Master sequence '"+P.sequenceName+"' not found"); return; }}
  app.project.activeSequence = master;
  log("master: "+master.name+"  V="+master.videoTracks.numTracks+" A="+master.audioTracks.numTracks);

  var nest = seqByName(P.nestName);
  var nestItem = findByName(app.project.rootItem, P.nestName);
  if(!nest || !nestItem){{ dumpLog(LOGP); alert("GAMEPLAY_NEST not found — is this the *_nest.prproj?"); return; }}

  // -- Phase 1: refill the nest INTERIOR with trimmed gameplay (video only).
  //    Promo is NEVER inside the nest, so no double-promo. Clear all nest
  //    tracks first, then lay the gameplay; nest interior length = gameplay. -- //
  for(var i=0;i<nest.videoTracks.numTracks;i++){{ clearVTrack(nest,i); }}
  for(var a=0;a<nest.audioTracks.numTracks;a++){{ clearATrack(nest,a); }}
  var ntrack = nest.videoTracks[0];
  for(var i=0;i<P.nestVideo.length;i++){{
    var s=P.nestVideo[i]; var it=ensureItem(s.src);
    if(!it){{ log("  MISSING source: "+s.src); continue; }}
    placeRange(ntrack,it,s.at,s["in"],s.out);
  }}
  log("nest interior refilled: "+P.nestVideo.length+" gameplay pieces (no promo inside)");

  // -- Phase 2: REUSE the existing keyed nest clip(s) on master V7. We must
  //    NEVER delete+recreate them — the 3 tuned Ultra Keys live on these
  //    trackItems and cannot be recreated. We only re-fit (interior in/out
  //    + position). Migration leaves: 2 adjacent keyed clips for a promo
  //    template, 1 for a no-promo template. -- //
  var cv = master.videoTracks[P.contentV];
  var keyed = [];
  for(var i=0;i<cv.clips.numItems;i++){{ keyed.push(cv.clips[i]); }}
  keyed.sort(function(a,b){{ return a.start.seconds - b.start.seconds; }});
  var want = P.nestMasterClips.length;  // 2 (promo) or 1 (no promo)
  if(keyed.length !== want){{
    dumpLog(LOGP);
    alert("Template mismatch: master V"+(P.contentV+1)+" has "+keyed.length
      +" clip(s) but this video needs "+want+".\\n\\n"
      +(want===2 ? "Razor the GAMEPLAY_NEST clip into 2 keyed halves "
        +"(Ctrl+K) during migration." : "Expected a single keyed nest clip.")
      +"\\nNo changes were made to clips.");
    return;
  }}
  // Shrink-before-grow ordering avoids transient overlap on the track:
  // fit clipA (index 0) first (it shrinks/frees space), then clipB.
  for(var i=0;i<P.nestMasterClips.length;i++){{
    var t=P.nestMasterClips[i];
    log("fit nest clip["+i+"] -> interior ["+t["in"]+","+t.out+"] @ "+t.at);
    fitClip(keyed[i], t["in"], t.out, t.at);
  }}

  // -- Phase 3: promo block into the V7 gap + audio on the gameplay track -- //
  if(P.promoPresent){{
    var promoIt=ensureItem(P.promoAsset);
    if(!promoIt){{ log("  MISSING promo asset: "+P.promoAsset); }}
    else {{
      for(var i=0;i<P.promoVideo.length;i++){{ var v=P.promoVideo[i];
        placeRange(cv,promoIt,v.at,v["in"],v.out); }}
      for(var i=0;i<P.promoAudio.length;i++){{ var au=P.promoAudio[i];
        placeRange(master.audioTracks[P.gameplayA],promoIt,au.at,au["in"],au.out); }}
      log("promo placed: "+P.promoVideo.length+" video + "+P.promoAudio.length+" audio subclips");
    }}
  }}

  // -- Phase 4: master gameplay audio (mirrors nest video, promo hole) ----- //
  clearATrack(master,P.gameplayA);
  for(var i=0;i<P.masterAudio.length;i++){{
    var m=P.masterAudio[i]; var it2=ensureItem(m.src);
    if(!it2){{ log("  MISSING audio source: "+m.src); continue; }}
    placeRange(master.audioTracks[P.gameplayA],it2,m.at,m["in"],m.out);
  }}
  log("placed "+P.masterAudio.length+" gameplay-audio pieces (+promo audio)");

  // -- Phase 5: stretch static decor + music to total. consolidateTrack()
  //    absorbs any collateral Ctrl+K splits from template migration. -------- //
  for(var d=0;d<P.decorV.length;d++){{
    var dc=consolidateTrack(master.videoTracks[P.decorV[d]]);
    if(dc){{ setSpan(dc,0,P.total); }}
  }}
  var mc=consolidateTrack(master.audioTracks[P.musicA]);
  if(mc){{
    var inP=safe(function(){{return mc.inPoint.seconds;}},0);
    safe(function(){{ mc.outPoint=inP+P.total; }});
    setSpan(mc,0,P.total);
  }}
  log("decor x"+P.decorV.length+" + music consolidated & stretched to "+P.total);

  // -- Phase 6: recompute promo-anchored overlays ------------------------- //
  var delta = P.promoPresent ? (P.promoInsertion - P.tplPromoStart) : 0;
  for(var o=0;o<P.overlayV.length;o++){{
    var ot=master.videoTracks[P.overlayV[o]];
    for(var ci=0; ci<ot.clips.numItems; ci++){{
      var cl=ot.clips[ci];
      var cs=safe(function(){{return cl.start.seconds;}},0);
      var ce=safe(function(){{return cl.end.seconds;}},0);
      var ns=cs, ne=ce;
      if(!P.promoPresent){{
        ne=P.total;                                   // LoE: just extend
      }} else if(Math.abs(ce-P.tplPromoStart)<=P.eps){{
        ne=P.promoInsertion;                           // phase-1: end at promo
      }} else if(Math.abs(cs-P.tplPromoEnd)<=P.eps){{
        ns=P.promoInsertion+P.promoBlock; ne=P.total;  // phase-3: after promo
      }} else if(cs>P.tplPromoStart-P.eps && ce<P.tplPromoEnd+P.eps){{
        ns=cs+delta; ne=ce+delta;                      // phase-2: shift w/ promo
      }} else if(cs<=P.eps && Math.abs(ce-P.tplTotal)<=P.eps){{
        ne=P.total;                                    // full-span overlay
      }}
      log("  V"+(P.overlayV[o]+1)+" clip["+ci+"] ["+cs.toFixed(2)+","+ce.toFixed(2)
          +"] -> ["+ns.toFixed(2)+","+ne.toFixed(2)+"]");
      setSpan(cl,ns,ne);
    }}
  }}

  dumpLog(LOGP);
  alert("REBUILD done (supervised — NOT rendered).\\n\\n"
    +"Master: "+master.name+"\\n"
    +"Total target: "+P.total.toFixed(1)+"s\\n"
    +"Promo: "+(P.promoPresent?("yes @ "+P.promoInsertion.toFixed(1)+"s"):"no")+"\\n\\n"
    +"Eyeball the timeline. Log: "+LOGP);
}})();
"""
    out = output_jsx or (TMP_DIR / f"{plan.game_slug}_{plan.video_slug}_rebuild.jsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(jsx, encoding="utf-8")
    return out


def render(*, plan: EditPlan, game: GameConfig, output_mp4: Path | None = None) -> Path:
    """CLI entrypoint. Returns the path to the generated rebuild .jsx."""
    return generate_rebuild_jsx(plan)
