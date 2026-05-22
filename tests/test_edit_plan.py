"""Phase 3 — edit-plan logic (pure parts, no ffmpeg/Premiere needed)."""

import json

import pytest

from youtube_automator.adobe.edit_plan import (
    EditPlan,
    Fragment,
    KeepSegment,
    PromoPlan,
    _snap_insertion,
    edges_keep_span,
    extract_promo_block,
    place_promo_split,
)
from youtube_automator.config import get_game
from youtube_automator.paths import TMP_DIR


def test_keep_and_fragment_duration_math():
    f = Fragment(
        index=0,
        path="x.mp4",
        probe_duration_sec=100.0,
        keep_segments=[
            KeepSegment(src_in_sec=0.0, src_out_sec=10.0),
            KeepSegment(src_in_sec=20.0, src_out_sec=35.0),
        ],
    )
    assert f.keep_segments[0].duration_sec == 10.0
    assert f.kept_duration_sec == 25.0


def test_snap_insertion_picks_nearest_boundary():
    boundaries = [30.0, 64.0, 71.0, 200.0]
    assert _snap_insertion(67.0, boundaries, 415.0) == 64.0  # |64-67|=3 < |71-67|=4
    assert _snap_insertion(69.0, boundaries, 415.0) == 71.0  # |71-69|=2 < |64-69|=5
    assert _snap_insertion(31.0, boundaries, 415.0) == 30.0
    # Ties resolve to the earlier boundary (min is stable).
    assert _snap_insertion(67.5, [64.0, 71.0], 415.0) == 64.0
    # No boundaries (exact mode) -> clamp to gameplay range.
    assert _snap_insertion(67.0, [], 415.0) == 67.0
    assert _snap_insertion(999.0, [], 415.0) == 415.0


def test_edit_plan_json_roundtrip():
    plan = EditPlan(
        game_slug="lom",
        video_slug="demo",
        fragments=[Fragment(index=0, path="a.mp4", probe_duration_sec=12.0)],
        promo=PromoPlan(present=False),
        gameplay_duration_sec=12.0,
        total_duration_sec=12.0,
    )
    again = EditPlan.model_validate_json(plan.model_dump_json())
    assert again.game_slug == "lom"
    assert again.fragments[0].probe_duration_sec == 12.0


def test_promo_block_preserves_internal_audio_cut():
    """The LoM promo's deliberate ~0.4s audio excision must survive extraction.

    Uses the real describe-dump if present (this dev machine); skipped
    elsewhere so the smoke suite stays portable.
    """
    dump = TMP_DIR / "lom_describe.json"
    if not dump.exists():
        pytest.skip("lom_describe.json not present (run describe_project.jsx)")

    promo = extract_promo_block(get_game("lom"))
    assert promo.present
    # Video governs the rigid block length (~52.23s).
    assert 52.0 <= promo.block_duration_sec <= 52.5

    vid = [s for s in promo.subclips if s.track_role == "content_video"]
    aud = sorted(
        (s for s in promo.subclips if s.track_role == "gameplay_audio"),
        key=lambda s: s.rel_start_sec,
    )
    # Video is continuous: each subclip starts where the previous ended.
    for a, b in zip(vid, vid[1:]):
        assert abs(a.rel_end_sec - b.rel_start_sec) < 1e-6
    # Audio has a gap (the spoken affiliate-code excision) between two pieces.
    gaps = [
        round(b.rel_start_sec - a.rel_end_sec, 4)
        for a, b in zip(aud, aud[1:])
        if b.rel_start_sec - a.rel_end_sec > 1e-6
    ]
    assert gaps, "expected a deliberate audio gap in the promo block"
    assert any(0.2 <= g <= 0.6 for g in gaps), f"audio cut gap out of range: {gaps}"


def test_edges_keep_span_trims_only_head_and_tail():
    dur = 100.0
    # leading dead air 0..3, an internal pause 40..45 (KEPT), trailing 96..100
    silences = [(0.0, 3.0), (40.0, 45.0), (96.0, 100.0)]
    a, b = edges_keep_span(
        silences, duration_sec=dur, keep_margin_sec=0.12, min_keep_sec=0.3
    )
    # internal pause survives; only edges trimmed (with margin around them)
    assert 2.5 < a < 3.0
    assert 96.0 < b < 96.5
    assert a < 40.0 < 45.0 < b  # the internal pause is inside the kept span


def test_edges_keep_span_no_edge_silence_keeps_all():
    a, b = edges_keep_span(
        [(50.0, 51.0)], duration_sec=120.0, keep_margin_sec=0.12, min_keep_sec=0.3
    )
    assert a == 0.0 and b == 120.0


def test_edges_keep_span_all_silence_safety_keeps_whole_clip():
    a, b = edges_keep_span(
        [(0.0, 30.0)], duration_sec=30.0, keep_margin_sec=0.12, min_keep_sec=0.3
    )
    assert (a, b) == (0.0, 30.0)


def test_edges_keep_span_absorbs_opening_junk_cluster():
    """Real 'comeback' recording: a burst of setup noise (sub-0.3s spoken
    gaps between early silences) precedes the scripted intro at 3.612s."""
    dur = 441.95
    silences = [
        (0.0, 0.4097), (0.5871, 1.4644), (1.7367, 2.5102), (2.7509, 3.6117),
        (62.0943, 62.6880), (430.1831, 430.9309), (438.2100, 441.9200),
    ]
    a, b = edges_keep_span(
        silences, duration_sec=dur, keep_margin_sec=0.12, min_keep_sec=0.3
    )
    # Lead-in fumbling (0 -> ~3.6) trimmed; real speech "hey whatsup" kept.
    assert 3.4 < a < 3.7
    # Trailing 3.7s of dead air trimmed; "bye bye cya" (ends 438.21) kept.
    assert 438.1 < b < 438.4


def test_edges_keep_span_keeps_short_real_interjection_when_no_cluster():
    """A clip that opens straight into a long spoken segment is NOT trimmed
    (the first silence is a genuine pause well into the speech)."""
    a, b = edges_keep_span(
        [(0.0, 0.5), (45.0, 46.0)], duration_sec=90.0,
        keep_margin_sec=0.12, min_keep_sec=0.3,
    )
    # Only the t=0 silence is leading junk; the 45s pause is real -> kept.
    assert 0.3 < a < 0.5
    assert b == 90.0


def test_place_promo_split_cuts_at_nearest_silence():
    # One whole take 0..300 kept; natural pauses at 64.5 and 130.
    fr = Fragment(
        index=0, path="A.mp4", probe_duration_sec=300.0,
        keep_segments=[KeepSegment(src_in_sec=0.0, src_out_sec=300.0)],
    )
    sils = {0: [(64.0, 65.0), (129.0, 131.0)]}
    at = place_promo_split([fr], sils, target=67.0, gameplay_dur=300.0, snap="silence")
    # split at the 64..65 pause (centre 64.5), nearest to target 67
    assert abs(at - 64.5) < 1e-6
    assert len(fr.keep_segments) == 2
    assert fr.keep_segments[0].src_out_sec == 64.5
    assert fr.keep_segments[1].src_in_sec == 64.5
    # total kept duration is unchanged by the split
    assert round(sum(k.duration_sec for k in fr.keep_segments), 4) == 300.0


def test_place_promo_split_exact_mode_no_silence_search():
    fr = Fragment(
        index=0, path="A.mp4", probe_duration_sec=300.0,
        keep_segments=[KeepSegment(src_in_sec=0.0, src_out_sec=300.0)],
    )
    at = place_promo_split([fr], {0: [(64.0, 65.0)]}, target=67.0,
                            gameplay_dur=300.0, snap="exact")
    assert abs(at - 67.0) < 1e-6
    assert fr.keep_segments[0].src_out_sec == 67.0


def test_loe_has_no_promo():
    promo = extract_promo_block(get_game("loe"))
    assert promo.present is False
    assert promo.subclips == []
