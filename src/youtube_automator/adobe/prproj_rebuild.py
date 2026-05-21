"""M1 — retime existing clips in a .prproj per an EditPlan (offline surgeon).

Scope of M1 (deliberately partial, to de-risk approach B with a real
Premiere open BEFORE building everything):

  * V7  : the 2 GAMEPLAY_NEST clips -> the promo-split layout (timeline +
          interior window). The Ultra Keys ride along untouched.
  * decor (V1..V6 minus hidden V2): span [0, total]. Collateral Ctrl+K
          splits are handled by laying the halves contiguously (same still).
  * music: span [0, total] with a continuous source window.
  * overlays (V8/V10/V11): recomputed by phase from the promo window.
  * V9/A3 intro, V2 hidden: untouched.

Deferred to M2/M3 (clip-count changes / new media): GAMEPLAY_NEST interior
refill, gameplay audio on A1, the promo block.
"""

from __future__ import annotations

from pathlib import Path

from pathlib import Path as _P

from .edit_plan import EditPlan, probe_duration_sec
from .premiere import _gameplay_pieces, compute_layout
from .prproj_xml import ClipRef, Project


def _inject_media(proj: Project, plan: EditPlan, L: dict, log: list[str]) -> dict:
    """Inject a fresh media cluster for every distinct recording + the promo.

    The template references its footage at the old recording paths; a real
    video uses new files. We clone the canonical gameplay/promo media
    cluster, repath it to the actual file and set its real duration. Returns
    {abs_path: media_handles} so cloned trackitems can be repointed.
    """
    # Blueprints must be the ACTUAL gameplay/promo recordings used by the
    # reference edit (same capture profile as the user's footage), not some
    # random .mp4 from the template's large media bin. Take them straight
    # from the GAMEPLAY_NEST interior + the content track.
    promo_needle = (plan.template_profile.get("promo", {}) or {}).get(
        "clip_name_contains", "PROMO"
    ).lower()
    gp_blueprint = None
    promo_blueprint = None
    nest = proj.sequence("GAMEPLAY_NEST")
    for _lbl, ct in proj.tracks(nest, "video"):
        for c in proj.clips(ct, _lbl):
            nm = c.name or ""
            if not nm:
                continue
            if promo_needle in nm.lower():
                promo_blueprint = promo_blueprint or nm
            elif nm.lower().endswith((".mp4", ".mov", ".mkv")):
                gp_blueprint = gp_blueprint or nm
    if plan.promo.present and promo_blueprint is None:
        master = proj.sequence(plan.template_profile.get("sequence_name", ""))
        for _lbl, ct in proj.tracks(master, "video"):
            for c in proj.clips(ct, _lbl):
                if promo_needle in (c.name or "").lower():
                    promo_blueprint = c.name
                    break
            if promo_blueprint:
                break

    injected: dict[str, dict] = {}
    # Distinct gameplay recordings from the plan.
    for fr in plan.fragments:
        path = _P(fr.path)
        if str(path) in injected or gp_blueprint is None:
            continue
        dur = probe_duration_sec(path)
        injected[str(path)] = proj.clone_media_cluster(gp_blueprint, path, dur)
        log.append(f"inject media: {path.name} ({dur:.1f}s) <- blueprint {gp_blueprint}")
    # Promo asset (make the project self-contained at assets/aptoide_ads/).
    if plan.promo.present and promo_blueprint and plan.promo.asset_path:
        ap = _P(plan.promo.asset_path)
        if ap.exists():
            dur = probe_duration_sec(ap)
            injected["__promo__"] = proj.clone_media_cluster(promo_blueprint, ap, dur)
            log.append(f"inject promo media: {ap.name} ({dur:.1f}s)")
    return injected

EPS = 0.6  # s — overlay phase classification tolerance (template is ~0.x off)


def _label(idx0: int, kind: str) -> str:
    return f"{'V' if kind == 'video' else 'A'}{idx0 + 1}"


def _retime_decor(clips: list[ClipRef], total: float) -> None:
    """Lay all clips on the track contiguously across [0, total] (stills)."""
    n = len(clips)
    if n == 1:
        clips[0].set_timeline(0.0, total)
        return
    step = total / n
    for i, c in enumerate(clips):
        c.set_timeline(round(i * step, 4), round((i + 1) * step, 4) if i < n - 1 else total)


def _retime_music(clips: list[ClipRef], total: float) -> None:
    """Span [0, total] keeping the source continuous (audio can't be held)."""
    if not clips:
        return
    base_in = clips[0].in_sec or 0.0
    n = len(clips)
    step = total / n
    for i, c in enumerate(clips):
        t0 = round(i * step, 4)
        t1 = total if i == n - 1 else round((i + 1) * step, 4)
        c.set_timeline(t0, t1)
        c.set_source(round(base_in + t0, 4), round(base_in + t1, 4))


def _retime_overlays(
    clips: list[ClipRef],
    *,
    promo_at: float,
    promo_end: float,
    total: float,
    tpl_start: float,
    tpl_end: float,
    tpl_total: float,
    has_promo: bool,
) -> list[str]:
    log: list[str] = []
    delta = (promo_at - tpl_start) if has_promo else 0.0
    for c in clips:
        cs = c.start_sec or 0.0
        ce = c.end_sec or 0.0
        ns, ne = cs, ce
        if not has_promo:
            ne = total
        elif abs(ce - tpl_start) <= EPS:                 # phase-1
            ne = promo_at
        elif abs(cs - tpl_end) <= EPS:                   # phase-3
            ns, ne = promo_end, total
        elif cs > tpl_start - EPS and ce < tpl_end + EPS:  # phase-2
            ns, ne = cs + delta, ce + delta
        elif cs <= EPS and abs(ce - tpl_total) <= EPS:   # full-span overlay
            ne = total
        c.set_timeline(round(max(0.0, ns), 4), round(ne, 4))
        log.append(f"  {c.track_label} {c.name!r} [{cs:.1f},{ce:.1f}]->[{ns:.1f},{ne:.1f}]")
    return log


def _rebuild_nest_interior(
    proj: Project, plan: EditPlan, log: list[str], injected: dict
) -> None:
    """M2 — refill GAMEPLAY_NEST with the trimmed gameplay (video only).

    Existing trackitems for each recording are cloned (full media linkage
    preserved) and retimed; no new media injection needed when the files
    are already in the project (the guideline case).
    """
    pieces = _gameplay_pieces(plan)
    nest = proj.sequence("GAMEPLAY_NEST")

    # Resolve a clone-source trackitem per distinct recording up-front.
    templates: dict[str, object] = {}
    for p in pieces:
        nm = _P(p["src"]).name
        if nm not in templates:
            t = proj.find_clip_template(nm) or proj.find_clip_template(_P(nm).stem)
            if t is None:
                log.append(f"  NEST: no clone template for {nm} — skipped")
            templates[nm] = t

    # Nest content video track = the nest video track that currently has clips.
    content_ct = None
    for _lbl, ct in proj.tracks(nest, "video"):
        if proj.clips(ct, _lbl):
            content_ct = ct
            break
    if content_ct is None:
        log.append("  NEST: no content video track found — skipped")
        return

    proj.clear_track(content_ct)
    for _lbl, ct in proj.tracks(nest, "audio"):   # nest is video-only
        proj.clear_track(ct)

    placed = 0
    for p in pieces:
        nm = _P(p["src"]).name
        tpl = templates.get(nm)
        if tpl is None:
            continue
        ref, new_vti = proj.clone_clip(tpl)
        ref.set_source(p["src_in"], p["src_out"])
        ref.set_timeline(p["g_start"], p["g_end"])
        med = injected.get(p["src"])
        if med:
            proj.repoint_clip_media(new_vti, med)
        proj.add_clip(content_ct, new_vti)
        placed += 1
    log.append(
        f"NEST interior: cleared + {placed}/{len(pieces)} gameplay pieces "
        f"(0 -> {plan.gameplay_duration_sec:.1f}s, video only)"
    )


def _place_audio_and_promo(
    proj: Project, plan: EditPlan, L: dict, log: list[str], injected: dict
) -> None:
    """M3 — master gameplay-audio track + the rigid promo block.

      * gameplay audio: mirror the nest video timeline (compute_layout's
        masterAudio), opening the promo hole.
      * promo: clone the promo trackitem(s) into the V7 gap (video) and onto
        the gameplay-audio track (audio). The audio list already encodes the
        deliberate ~0.4s code excision as a gap between sub-clips.
    """
    by_label = proj.tracks_by_label(plan.template_profile.get("sequence_name", ""))
    ga = by_label.get(_label(L["gameplayA"], "audio"))
    cv = by_label.get(_label(L["contentV"], "video"))
    if ga is None:
        log.append("  M3: gameplay-audio track not found — skipped")
        return

    # Clone-source per recording (audio) + the promo (video & audio).
    aud_tpl: dict[str, object] = {}
    for m in L["masterAudio"]:
        nm = _P(m["src"]).name
        if nm not in aud_tpl:
            aud_tpl[nm] = proj.find_clip_template(nm, "audio") or proj.find_clip_template(
                _P(nm).stem, "audio"
            )
    promo_v = proj.find_clip_template("PROMO", "video") if L["promoPresent"] else None
    promo_a = proj.find_clip_template("PROMO", "audio") if L["promoPresent"] else None

    proj.clear_track(ga)  # drop the old reference audio entirely

    placed = 0
    for m in L["masterAudio"]:
        tpl = aud_tpl.get(_P(m["src"]).name)
        if tpl is None:
            continue
        ref, vti = proj.clone_clip(tpl)
        ref.set_source(m["src_in"], m["src_out"])
        ref.set_timeline(m["at"], round(m["at"] + (m["src_out"] - m["src_in"]), 4))
        med = injected.get(m["src"])
        if med:
            proj.repoint_clip_media(vti, med)
        proj.add_clip(ga, vti)
        placed += 1
    log.append(f"A1 gameplay audio: cleared + {placed}/{len(L['masterAudio'])} pieces")

    if L["promoPresent"] and promo_v is not None and promo_a is not None:
        pm = injected.get("__promo__")
        for v in L["promoVideo"]:
            ref, vti = proj.clone_clip(promo_v)
            ref.set_source(v["src_in"], v["src_out"])
            ref.set_timeline(v["at"], round(v["at"] + (v["src_out"] - v["src_in"]), 4))
            if pm:
                proj.repoint_clip_media(vti, pm)
            proj.add_clip(cv, vti)
        for a in L["promoAudio"]:
            ref, vti = proj.clone_clip(promo_a)
            ref.set_source(a["src_in"], a["src_out"])
            ref.set_timeline(a["at"], round(a["at"] + (a["src_out"] - a["src_in"]), 4))
            if pm:
                proj.repoint_clip_media(vti, pm)
            proj.add_clip(ga, vti)
        log.append(
            f"promo block: {len(L['promoVideo'])} video on V{L['contentV']+1} + "
            f"{len(L['promoAudio'])} audio (0.4s code cut preserved)"
        )
    elif L["promoPresent"]:
        log.append("  M3: promo template not found in project — promo skipped")


def rebuild(plan: EditPlan, template_path: Path, out_path: Path) -> tuple[Path, list[str]]:
    """Produce a retimed .prproj from the template. Returns (path, log)."""
    L = compute_layout(plan)
    seq_name = plan.template_profile.get("sequence_name", "")
    proj = Project.load(template_path)
    m = proj.map_sequence(seq_name)
    log: list[str] = [f"rebuild {plan.game_slug}/{plan.video_slug} -> {out_path.name}"]

    # -- V7: the 2 keyed nest clips -> promo-split layout ------------------- #
    v7 = m.get(_label(L["contentV"], "video"), [])
    targets = L["nestMasterClips"]
    if len(v7) == len(targets):
        for c, t in zip(v7, targets):
            c.set_timeline(t["at"], round(t["at"] + (t["out"] - t["in"]), 4))
            c.set_source(t["in"], t["out"])
        log.append(f"V7: {len(v7)} nest clip(s) retimed {[(t['at']) for t in targets]}")
    else:
        log.append(f"V7 MISMATCH: track has {len(v7)} clips, plan wants {len(targets)} — skipped")

    # -- decor -------------------------------------------------------------- #
    for idx0 in L["decorV"]:
        lbl = _label(idx0, "video")
        if lbl in m:
            _retime_decor(m[lbl], L["total"])
            log.append(f"{lbl}: decor -> [0,{L['total']:.1f}] ({len(m[lbl])} clip(s))")

    # -- music -------------------------------------------------------------- #
    mlbl = _label(L["musicA"], "audio")
    if mlbl in m:
        _retime_music(m[mlbl], L["total"])
        log.append(f"{mlbl}: music -> [0,{L['total']:.1f}] ({len(m[mlbl])} clip(s))")

    # -- overlays ----------------------------------------------------------- #
    for idx0 in L["overlayV"]:
        lbl = _label(idx0, "video")
        if lbl in m:
            log += _retime_overlays(
                m[lbl],
                promo_at=L["promoInsertion"],
                promo_end=L["promoInsertion"] + L["promoBlock"],
                total=L["total"],
                tpl_start=L["tplPromoStart"],
                tpl_end=L["tplPromoEnd"],
                tpl_total=L["tplTotal"],
                has_promo=L["promoPresent"],
            )

    # -- M2b: inject fresh media for the real recordings + promo asset ----- #
    injected = _inject_media(proj, plan, L, log)

    # -- M2: refill the nest interior with the real trimmed gameplay ------- #
    _rebuild_nest_interior(proj, plan, log, injected)

    # -- M3: master gameplay audio + the rigid promo block ----------------- #
    _place_audio_and_promo(proj, plan, L, log, injected)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proj.save(out_path)
    log.append(f"saved {out_path}")
    return out_path, log
