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
    assert games["legend_of_mushroom"].slug == "lom"
    assert games["legend_of_elements"].slug == "loe"


def test_guardrails_banned_phrases():
    s = get_settings()
    banned = [b.lower() for b in s.contract_guardrails.description_must_not_contain]
    # §4.11: never claim Aptoide hosts paid apps for free
    assert any("paid apps free" in b or "apps de pago gratis" in b for b in banned)
