"""Phase 3 — Premiere rebuild timeline math (pure, no Premiere/ffmpeg)."""

from youtube_automator.adobe.edit_plan import (
    EditPlan,
    Fragment,
    KeepSegment,
    PromoPlan,
    PromoSubclip,
)
from youtube_automator.adobe.premiere import (
    _gameplay_pieces,
    _master_audio_pieces,
    _nest_master_clips,
    compute_layout,
    track_index,
)


def _plan(promo: bool) -> EditPlan:
    # Two fragments, simple keep segments -> gameplay timeline 0..30.
    frags = [
        Fragment(
            index=0, path="A.mp4", probe_duration_sec=20.0,
            keep_segments=[
                KeepSegment(src_in_sec=0.0, src_out_sec=10.0),   # g 0..10
                KeepSegment(src_in_sec=12.0, src_out_sec=22.0),  # g 10..20
            ],
        ),
        Fragment(
            index=1, path="B.mp4", probe_duration_sec=15.0,
            keep_segments=[KeepSegment(src_in_sec=1.0, src_out_sec=11.0)],  # g 20..30
        ),
    ]
    pp = PromoPlan(present=False)
    insertion = 0.0
    total = 30.0
    if promo:
        pp = PromoPlan(
            present=True,
            asset_path="promo.mp4",
            block_duration_sec=8.0,
            tpl_start_sec=74.15,
            tpl_end_sec=126.38,
            subclips=[
                PromoSubclip(track_role="content_video", rel_start_sec=0.0,
                             rel_end_sec=8.0, src_in_sec=0.0, src_out_sec=8.0),
                PromoSubclip(track_role="gameplay_audio", rel_start_sec=0.0,
                             rel_end_sec=5.0, src_in_sec=0.0, src_out_sec=5.0),
                PromoSubclip(track_role="gameplay_audio", rel_start_sec=5.4,
                             rel_end_sec=8.0, src_in_sec=5.4, src_out_sec=8.0),
            ],
        )
        insertion = 15.0   # falls inside the 2nd keep (g 10..20) -> straddle
        total = 38.0
    return EditPlan(
        game_slug="lom", video_slug="t",
        fragments=frags, promo=pp,
        gameplay_duration_sec=30.0,
        promo_insertion_sec=insertion,
        total_duration_sec=total,
        tpl_total_sec=525.92,
        template_profile={
            "sequence_name": "S",
            "content_video_track": "V7",
            "gameplay_audio_track": "A1",
            "music_track": "A2",
            "static_decor_video_tracks": ["V1", "V3"],
            "overlay_tracks": ["V8", "V10", "V11"],
        },
    )


def test_track_index():
    assert track_index("V7") == 6
    assert track_index("A1") == 0
    assert track_index("V11") == 10


def test_gameplay_pieces_are_contiguous():
    pieces = _gameplay_pieces(_plan(promo=False))
    assert [p["g_start"] for p in pieces] == [0.0, 10.0, 20.0]
    assert [p["g_end"] for p in pieces] == [10.0, 20.0, 30.0]
    # interior timeline length == gameplay duration
    assert pieces[-1]["g_end"] == 30.0


def test_master_audio_no_promo_is_identity():
    plan = _plan(promo=False)
    pieces = _gameplay_pieces(plan)
    ma = _master_audio_pieces(plan, pieces)
    assert [m["at"] for m in ma] == [0.0, 10.0, 20.0]


def test_master_audio_splits_straddling_piece_and_opens_hole():
    plan = _plan(promo=True)  # insertion @ 15 inside g[10,20]
    pieces = _gameplay_pieces(plan)
    ma = _master_audio_pieces(plan, pieces)
    # piece g[0,10] before cut -> unchanged
    assert ma[0]["at"] == 0.0
    # straddling g[10,20] split at 15: part1 ends at master 10, part2 jumps
    # past the 8s promo block -> 15 + 8 = 23
    ats = [round(m["at"], 4) for m in ma]
    assert 10.0 in ats and 23.0 in ats
    # last piece g[20,30] fully after cut -> 20 + 8 = 28
    assert ats[-1] == 28.0
    # split preserves source continuity (src_mid shared)
    straddle = [m for m in ma if m["at"] in (10.0, 23.0)]
    assert straddle[0]["src_out"] == straddle[1]["src_in"]


def test_nest_master_clips_open_promo_gap():
    one = _nest_master_clips(_plan(promo=False))
    assert one == [{"in": 0.0, "out": 30.0, "at": 0.0}]
    two = _nest_master_clips(_plan(promo=True))
    assert two[0] == {"in": 0.0, "out": 15.0, "at": 0.0}
    # second nest piece resumes at insertion + block (15 + 8)
    assert two[1] == {"in": 15.0, "out": 30.0, "at": 23.0}


def test_compute_layout_resolves_tracks_and_lists():
    L = compute_layout(_plan(promo=True))
    assert L["contentV"] == 6 and L["gameplayA"] == 0 and L["musicA"] == 1
    assert L["decorV"] == [0, 2] and L["overlayV"] == [7, 9, 10]
    assert len(L["promoVideo"]) == 1 and len(L["promoAudio"]) == 2
    # promo video placed at the insertion point
    assert L["promoVideo"][0]["at"] == 15.0
    # the 0.4s code excision survives as a gap between the 2 audio subclips
    a0, a1 = L["promoAudio"]
    assert a0["at"] == 15.0 and round(a1["at"] - 15.0, 4) == 5.4
