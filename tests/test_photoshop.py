"""Photoshop thumbnail helpers (pure parts; no COM/Photoshop required)."""

from pathlib import Path

from youtube_automator.adobe.photoshop import (
    discover_templates,
    next_template_index,
    rotation_index,
    split_thumbnail_copy,
)
from youtube_automator.config import get_game


def test_split_first_space():
    assert split_thumbnail_copy("BEGINNER GUIDE") == ("BEGINNER", "GUIDE")
    assert split_thumbnail_copy("NEW DUNGEON SECRETS") == ("NEW", "DUNGEON SECRETS")
    assert split_thumbnail_copy("ONE") == ("ONE", "")
    assert split_thumbnail_copy("") == ("", "")
    assert split_thumbnail_copy("  trim  me  ") == ("trim", "me")


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
