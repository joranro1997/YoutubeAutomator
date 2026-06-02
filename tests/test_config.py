"""Smoke tests: configs load and validate."""

from youtube_automator.config import get_games, get_settings


def test_settings_loads():
    s = get_settings()
    assert s.pipeline.videos_per_week_target == 2
    assert "default_lom" in s.description_templates
    assert "{affiliate_code}" in s.contract_guardrails.description_must_contain


def test_games_load():
    games = get_games()
    assert "legend_of_mushroom" in games
    assert "legend_of_elements" in games
    assert "duck_survival" in games
    assert games["legend_of_mushroom"].slug == "lom"
    assert games["legend_of_elements"].slug == "loe"
    assert games["duck_survival"].slug == "dsv"


def test_duck_survival_profile():
    """The 3rd game is wired for SEO + edit: template, audio routing, desc."""
    from youtube_automator.config import get_game, get_settings

    g = get_game("dsv")
    assert g.display_name == "Duck Survival"
    # LoM audio routing (voice A1 / music A2), no promo block yet.
    assert g.premiere_template.gameplay_audio_track == "A1"
    assert g.premiere_template.music_track == "A2"
    assert g.premiere_template.promo.present is False
    assert g.premiere_template.template_filename == "dsv_nest.prproj"
    # Its own description template + hashtag line exist.
    s = get_settings()
    assert g.sponsorship.description_template_id == "default_dsv"
    assert "default_dsv" in s.description_templates
    assert "default_dsv" in s.hashtag_lines


def test_recent_uploads_keywords_derived_for_new_game():
    """A new game matches its uploads with no per-slug code change."""
    from youtube_automator.config import get_game
    from youtube_automator.ideation.recent_uploads import _game_keywords, _matches_game

    g = get_game("dsv")
    assert "duck survival" in _game_keywords(g)
    assert _matches_game("INSANE NEW Duck Survival Update!", g) is True
    assert _matches_game("legend of mushroom nightmare dungeon", g) is False


def test_premiere_template_profiles():
    games = get_games()
    lom = games["legend_of_mushroom"].premiere_template
    loe = games["legend_of_elements"].premiere_template

    # Both put recorded gameplay on V7.
    assert lom.content_video_track == "V7"
    assert loe.content_video_track == "V7"

    # Audio routing is REVERSED between the two templates — the whole reason
    # track roles live in config instead of being hardcoded.
    assert lom.gameplay_audio_track == "A1" and lom.music_track == "A2"
    assert loe.gameplay_audio_track == "A2" and loe.music_track == "A1"

    # LoM has the rigid Aptoide promo block; LoE does not (yet).
    assert lom.promo.present is True
    assert lom.promo.asset_filename == "lom.mp4"
    assert loe.promo.present is False

    # V2 is hidden in Premiere but still stretched as decor so the timeline
    # looks tidy; only the empty meta tracks at the tail are ignored.
    assert "V2" in lom.static_decor_video_tracks
    assert "V2" in loe.static_decor_video_tracks
    assert "V12" in lom.ignore_video_tracks
    assert "V13" in loe.ignore_video_tracks

    # Silence-cut defaults are present and sane for energetic VO.
    assert lom.silence.min_silence_sec == 0.4
    assert lom.silence.keep_margin_sec == 0.12


def test_guardrails_banned_phrases():
    s = get_settings()
    banned = [b.lower() for b in s.contract_guardrails.description_must_not_contain]
    # §4.11: never claim Aptoide hosts paid apps for free
    assert any("paid apps free" in b or "apps de pago gratis" in b for b in banned)
