"""Photoshop thumbnail helpers (pure parts; no COM/Photoshop required)."""

from pathlib import Path

from youtube_automator.adobe.photoshop import (
    _overflowing,
    _read_fit,
    _shorten_for_fit,
    discover_templates,
    next_template_index,
    rotation_index,
    split_thumbnail_copy,
)
from youtube_automator.config import PhotoshopTemplate, get_game


def test_split_first_space():
    assert split_thumbnail_copy("BEGINNER GUIDE") == ("BEGINNER", "GUIDE")
    assert split_thumbnail_copy("NEW DUNGEON SECRETS") == ("NEW", "DUNGEON SECRETS")
    assert split_thumbnail_copy("ONE") == ("ONE", "")
    assert split_thumbnail_copy("") == ("", "")
    assert split_thumbnail_copy("  trim  me  ") == ("trim", "me")


def test_split_caps_at_three_words():
    # Over-long copy is hard-capped to 3 words (first top, next two bottom).
    assert split_thumbnail_copy("FREE STUFF AS NEW PLAYER") == ("FREE", "STUFF AS")
    assert split_thumbnail_copy("A B C D E", max_words=3) == ("A", "B C")
    # Exactly 3 words is unchanged.
    assert split_thumbnail_copy("FREE NEW HEROES") == ("FREE", "NEW HEROES")


def test_split_newline():
    assert split_thumbnail_copy("TOP\nBOTTOM", strategy="newline") == ("TOP", "BOTTOM")
    # falls back to first_space behaviour when no newline
    assert split_thumbnail_copy("NEW SYSTEM", strategy="newline") == ("NEW", "SYSTEM")


def test_discover_templates_alphabetical(tmp_path: Path, monkeypatch):
    # point photoshop_templates_dir at a sandbox
    import youtube_automator.adobe.photoshop as ps_mod

    sandbox = tmp_path / "ps"
    (sandbox / "lom").mkdir(parents=True)
    for name in ("LoM-91.psd", "LoM-3.psd", "LoM-80.psd"):
        (sandbox / "lom" / name).write_bytes(b"x")
    (sandbox / "lom" / "notes.txt").write_text("ignore me")
    monkeypatch.setattr(ps_mod, "photoshop_templates_dir", lambda: sandbox)

    found = discover_templates(get_game("lom"))
    assert [p.name for p in found] == ["LoM-3.psd", "LoM-80.psd", "LoM-91.psd"]


def test_rotation_index_counts_pngs(tmp_path: Path, monkeypatch):
    import youtube_automator.adobe.photoshop as ps_mod

    outputs = tmp_path / "outputs"
    g = "lom"
    (outputs / g / "v1").mkdir(parents=True)
    (outputs / g / "v1" / "v1.png").write_bytes(b"x")
    (outputs / g / "v2").mkdir(parents=True)            # no png yet
    (outputs / g / "v3").mkdir(parents=True)
    (outputs / g / "v3" / "v3.png").write_bytes(b"x")

    monkeypatch.setattr(ps_mod, "OUTPUTS_DIR", outputs)
    assert rotation_index(get_game("lom")) == 2


def test_next_template_index_cycles_and_is_stable(tmp_path: Path, monkeypatch):
    import youtube_automator.adobe.photoshop as ps_mod

    outputs = tmp_path / "outputs"
    monkeypatch.setattr(ps_mod, "OUTPUTS_DIR", outputs)
    g = get_game("lom")

    # 4 templates -> consecutive NEW slugs walk 0,1,2,3,0,...
    assert next_template_index(g, "a", 4) == 0
    assert next_template_index(g, "b", 4) == 1
    assert next_template_index(g, "c", 4) == 2
    assert next_template_index(g, "d", 4) == 3
    assert next_template_index(g, "e", 4) == 0   # wraps

    # Re-rendering an existing slug REUSES its template (idempotent).
    assert next_template_index(g, "b", 4) == 1
    assert next_template_index(g, "b", 4) == 1
    # ...and does not disturb the rotation for the next new slug.
    assert next_template_index(g, "f", 4) == 1   # follows 'e'(0) -> 1


def test_next_template_index_survives_state_reload(tmp_path: Path, monkeypatch):
    import youtube_automator.adobe.photoshop as ps_mod

    outputs = tmp_path / "outputs"
    monkeypatch.setattr(ps_mod, "OUTPUTS_DIR", outputs)
    g = get_game("lom")

    assert next_template_index(g, "v1", 3) == 0
    assert next_template_index(g, "v2", 3) == 1
    # A fresh process reads the persisted state file and keeps cycling.
    assert next_template_index(g, "v3", 3) == 2
    assert next_template_index(g, "v4", 3) == 0


# --------------------------------------------------------------------------- #
# Thumbnail text auto-fit (anti-overflow) — pure parts, no Photoshop needed.
# --------------------------------------------------------------------------- #
def test_autofit_config_defaults():
    pt = PhotoshopTemplate()
    assert pt.autofit_text is True
    assert 0 < pt.text_fit_margin < 0.5
    assert 0 < pt.text_fit_min_scale < 1


def test_autofit_config_rejects_out_of_range():
    import pytest
    from pydantic import ValidationError

    PhotoshopTemplate(text_fit_margin=0.2, text_fit_min_scale=0.5)  # valid override ok
    with pytest.raises(ValidationError):
        PhotoshopTemplate(text_fit_margin=0.6)        # >= 0.5 -> usable area <= 0
    with pytest.raises(ValidationError):
        PhotoshopTemplate(text_fit_min_scale=1.5)     # > 1 would never shrink


def test_overflowing_detects_any_clipped_so():
    assert _overflowing([{"role": "top", "overflow": False},
                         {"role": "bottom", "overflow": True}]) is True
    assert _overflowing([{"role": "top", "overflow": False}]) is False
    assert _overflowing([]) is False


def test_shorten_for_fit_drops_trailing_word_of_overflowing_side():
    fit = [{"role": "top", "overflow": False},
           {"role": "bottom", "overflow": True}]
    top, bottom, changed = _shorten_for_fit("INSANE", "NEW META BUILD GUIDE", fit)
    assert changed is True
    assert top == "INSANE"                 # top didn't overflow -> untouched
    assert bottom == "NEW META BUILD"      # last word dropped


def test_shorten_for_fit_leaves_single_word_alone():
    fit = [{"role": "top", "overflow": True}, {"role": "bottom", "overflow": False}]
    top, bottom, changed = _shorten_for_fit("SUPERCALIFRAGILISTIC", "GUIDE", fit)
    assert changed is False                # don't mangle a lone word
    assert top == "SUPERCALIFRAGILISTIC"


def test_shorten_for_fit_noop_when_nothing_overflows():
    fit = [{"role": "top", "overflow": False}, {"role": "bottom", "overflow": False}]
    assert _shorten_for_fit("NEW", "DUNGEON", fit) == ("NEW", "DUNGEON", False)


def test_read_fit_tolerates_missing_and_malformed(tmp_path: Path):
    assert _read_fit(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _read_fit(bad) == []
    good = tmp_path / "good.json"
    good.write_text('[{"role":"top","overflow":false,"shrunk":true,"final_scale":0.7}]',
                    encoding="utf-8")
    assert _read_fit(good)[0]["role"] == "top"
    # A list containing non-dict junk is filtered, not crashed on.
    mixed = tmp_path / "mixed.json"
    mixed.write_text('["x", 42, {"role":"bottom","overflow":true}]', encoding="utf-8")
    parsed = _read_fit(mixed)
    assert parsed == [{"role": "bottom", "overflow": True}]
    assert _overflowing(parsed) is True
