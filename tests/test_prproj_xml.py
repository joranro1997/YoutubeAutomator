"""Offline .prproj surgeon — traversal + gzip round-trip + tick math."""

from pathlib import Path

import pytest

from youtube_automator.adobe.prproj_xml import (
    Project,
    sec_to_ticks,
    ticks_to_sec,
)
from youtube_automator.paths import premiere_templates_dir

TPL = premiere_templates_dir() / "lom_nest.prproj"
DSV_TPL = premiere_templates_dir() / "dsv_nest.prproj"
SEQ = "2023-03-23 20-59-52"


def test_tick_conversions_roundtrip():
    assert sec_to_ticks(1.0) == 254016000000
    assert abs(ticks_to_sec(254016000000) - 1.0) < 1e-9
    assert abs(ticks_to_sec(sec_to_ticks(66.9549)) - 66.9549) < 1e-4
    assert ticks_to_sec(None) is None


@pytest.mark.skipif(not TPL.exists(), reason="lom_nest.prproj not present")
def test_master_traversal_matches_known_structure():
    p = Project.load(TPL)
    m = p.map_sequence(SEQ)

    # V7 = the two keyed GAMEPLAY_NEST clips (nest referenced like a clip,
    # with its own in/out window).
    v7 = m["V7"]
    assert len(v7) == 2
    assert all(c.name == "GAMEPLAY_NEST" for c in v7)
    assert v7[0].start_sec == 0.0
    assert v7[1].end_sec is not None and v7[1].end_sec > 500
    assert v7[0].in_sec is not None  # nest has a source window too

    # Decor tracks are single full-length stills.
    assert len(m["V3"]) == 1 and m["V3"][0].name == "black.png"
    assert m["V6"][0].name == "green background.png"

    # 3-phase overlays present.
    assert len(m["V8"]) == 3
    assert len(m["V11"]) == 2


@pytest.mark.skipif(not DSV_TPL.exists(), reason="dsv_nest.prproj not present")
def test_tracks_identified_by_type_not_trackgroup_index():
    """Regression: dsv_nest ships with the video TrackGroup at Index 0 and
    audio at Index 1 (the reverse of lom_nest/loe). ``tracks()`` must key on
    the container element type, not the numeric Index, or every track is
    mislabelled — which dropped the gameplay video and doubled the audio.
    """
    p = Project.load(DSV_TPL)
    m = p.map_sequence(SEQ)
    # V7 = the (single, no-promo) gameplay nest clip; A1 = the recording's
    # voice track; A2 = the continuous music. If the group selection regresses
    # to Index-based, these labels point at the wrong (swapped) tracks.
    assert [c.name for c in m["V7"]] == ["GAMEPLAY_NEST"]
    assert len(m["A1"]) == 1 and (m["A1"][0].name or "").lower().endswith(".mp4")
    assert [c.name for c in m["A2"]] == ["videoplayback.mp3"]


@pytest.mark.skipif(not TPL.exists(), reason="lom_nest.prproj not present")
def test_gzip_roundtrip_preserves_structure(tmp_path: Path):
    p = Project.load(TPL)
    before = {k: len(v) for k, v in p.map_sequence(SEQ).items()}
    out = tmp_path / "rt.prproj"
    p.save(out)
    after = {k: len(v) for k, v in Project.load(out).map_sequence(SEQ).items()}
    assert before == after


def test_compute_layout_master_audio_keys():
    """Guards the M3 key contract (a wrong key silently broke promo audio)."""
    import json

    from youtube_automator.adobe.edit_plan import EditPlan
    from youtube_automator.adobe.premiere import compute_layout

    plan_path = Path("data/outputs/lom/guideline/edit_plan.json")
    if not plan_path.exists():
        pytest.skip("guideline edit_plan not present")
    L = compute_layout(EditPlan.model_validate_json(plan_path.read_text()))
    for m in L["masterAudio"]:
        assert {"src", "src_in", "src_out", "at"} <= set(m)
    for v in L["promoVideo"] + L["promoAudio"]:
        assert {"src_in", "src_out", "at"} <= set(v)


@pytest.mark.skipif(not TPL.exists(), reason="lom_nest.prproj not present")
def test_full_rebuild_reproduces_reference_structure(tmp_path: Path):
    """M1+M2+M3 end-to-end at the data level vs the known reference shape."""
    import json

    from youtube_automator.adobe.edit_plan import EditPlan
    from youtube_automator.adobe.prproj_rebuild import rebuild

    plan_path = Path("data/outputs/lom/guideline/edit_plan.json")
    if not plan_path.exists():
        pytest.skip("guideline edit_plan not present")
    plan = EditPlan.model_validate_json(plan_path.read_text())
    out, _log = rebuild(plan, TPL, tmp_path / "g.prproj")

    m = Project.load(out).map_sequence(SEQ)
    # V7 = 2 keyed nest clips + 2 promo video clips, contiguous, total≈promo end.
    v7 = sorted(m["V7"], key=lambda c: c.start_sec or 0)
    assert [c.name for c in v7].count("GAMEPLAY_NEST") == 2
    assert sum("PROMO" in (c.name or "") for c in v7) == 2
    # nest interior = the 3 trimmed gameplay pieces, video only.
    nest = Project.load(out).map_sequence("GAMEPLAY_NEST")
    vlabel = next(k for k in nest if k.startswith("V") and nest[k])
    assert len(nest[vlabel]) == 3
    # A1 = 3 gameplay-audio pieces + 3 promo-audio pieces (incl. 0.1s sliver).
    a1 = m["A1"]
    assert sum("PROMO" in (c.name or "") for c in a1) == 3
    # the deliberate ~0.4s code excision survives as an A1 gap.
    promo_a = sorted(
        (c for c in a1 if "PROMO" in (c.name or "")), key=lambda c: c.start_sec or 0
    )
    gaps = [
        round((b.start_sec or 0) - (a.end_sec or 0), 2)
        for a, b in zip(promo_a, promo_a[1:])
        if (b.start_sec or 0) - (a.end_sec or 0) > 0.01
    ]
    assert any(0.2 <= g <= 0.6 for g in gaps), f"code-cut gap missing: {gaps}"


@pytest.mark.skipif(not TPL.exists(), reason="lom_nest.prproj not present")
def test_m2b_injection_makes_project_self_contained(tmp_path: Path):
    """Rebuilt clips must reference the edit_plan's media (assets/), not the
    template's old D:\\WoW Videos / C:\\Users paths (production blocker)."""
    import gzip
    import xml.etree.ElementTree as ET

    from youtube_automator.adobe.edit_plan import EditPlan
    from youtube_automator.adobe.prproj_rebuild import rebuild

    plan_path = Path("data/outputs/lom/guideline/edit_plan.json")
    if not plan_path.exists():
        pytest.skip("guideline edit_plan not present")
    plan = EditPlan.model_validate_json(plan_path.read_text())
    out, _ = rebuild(plan, TPL, tmp_path / "g.prproj")

    p = Project.load(out)

    def media_file(clipref) -> str:
        clip = clipref._clip
        src = clip.find("Source") if clip is not None else None
        ms = p._by_id.get(src.get("ObjectRef")) if src is not None and src.get("ObjectRef") else None
        mref = ms.find("MediaSource/Media") if ms is not None else None
        media = p._by_id.get(mref.get("ObjectURef")) if mref is not None else None
        return (media.findtext("ActualMediaFilePath") or "") if media is not None else ""

    nest = p.map_sequence("GAMEPLAY_NEST")
    vlabel = next(k for k in nest if k.startswith("V") and nest[k])
    nest_paths = [media_file(c) for c in nest[vlabel]]
    # Must point under the repo's assets/ (where the recordings live now),
    # never the template's stale capture paths.
    assert nest_paths and all("assets" in mp for mp in nest_paths), nest_paths

    a1 = p.map_sequence(SEQ)["A1"]
    promo = [media_file(c) for c in a1 if "PROMO" in (c.name or "")]
    assert promo and all("aptoide_ads" in mp for mp in promo), promo
    # the template's stale capture paths must not leak into rebuilt clips
    assert not any("WoW Videos" in mp for mp in nest_paths)


@pytest.mark.skipif(not TPL.exists(), reason="lom_nest.prproj not present")
def test_mutation_persists_through_save(tmp_path: Path):
    p = Project.load(TPL)
    clip = p.map_sequence(SEQ)["V3"][0]      # a decor still
    clip.set_timeline(0.0, 467.34)
    out = tmp_path / "m.prproj"
    p.save(out)
    again = Project.load(out).map_sequence(SEQ)["V3"][0]
    assert abs((again.end_sec or 0) - 467.34) < 1e-3
