"""Photoshop thumbnail automation — driven from Python via COM.

Unlike Premiere (which needed a CEP panel to dodge missing CLI script
support), Photoshop exposes a stable COM interface that pywin32 can talk
to directly. Each thumbnail render is one synchronous call:

    photoshop.Application.DoJavaScript(<our JSX> + a call)

The JSX (scripts/jsx/yta_thumbnail.jsx) opens the .psd template, edits
the two top text Smart Objects (top word + bottom phrase), and exports
a PNG. No queue file, no CEP, no F5.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ..config import GameConfig
from ..paths import OUTPUTS_DIR, REPO_ROOT, TMP_DIR, photoshop_templates_dir

JSX_FILE = REPO_ROOT / "scripts" / "jsx" / "yta_thumbnail.jsx"
PSD_EXT = {".psd", ".psb"}

# Pin Photoshop 2021 (v22) — the user also has 2024 installed and its
# default ProgID would otherwise win (and 2024 errors on scratch disks
# in this setup). Override either via env.
DEFAULT_PHOTOSHOP_EXE = r"C:\Program Files\Adobe\Adobe Photoshop 2021\Photoshop.exe"
DEFAULT_PHOTOSHOP_PROGID = "Photoshop.Application.22"


def photoshop_exe() -> Path:
    """Photoshop executable. Env override, else auto-detect under Program
    Files\\Adobe (username-independent), PREFERRING 2021 — the user also has
    2024 installed and 2024 errors on scratch disks in this setup."""
    env = os.getenv("PHOTOSHOP_EXE")
    if env:
        return Path(env)
    from .auto_render import _find_adobe_exe
    return _find_adobe_exe(
        ["Adobe Photoshop 2021"], "Adobe Photoshop *",
        "Photoshop.exe", DEFAULT_PHOTOSHOP_EXE,
    )


def photoshop_progid() -> str:
    return os.getenv("PHOTOSHOP_PROGID", DEFAULT_PHOTOSHOP_PROGID)


def _suppress_script_warning() -> None:
    """Add `WarnRunningScripts 0` to PSUserConfig.txt so Photoshop doesn't
    prompt before running our .jsx. Idempotent. Takes effect after the
    next PS restart (so the dialog may still show ONCE)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    # Derive the PS version folder from the resolved exe (e.g. "Adobe
    # Photoshop 2021") so this lands in the RIGHT settings dir on any machine.
    ps_ver = photoshop_exe().parent.name or "Adobe Photoshop 2021"
    cfg_dir = Path(appdata) / "Adobe" / ps_ver / f"{ps_ver} Settings"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "PSUserConfig.txt"
    existing = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    if "WarnRunningScripts" in existing:
        return
    with cfg.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("WarnRunningScripts 0\n")


def discover_templates(game: GameConfig) -> list[Path]:
    """All .psd templates for a game, alphabetical (= rotation order)."""
    folder = photoshop_templates_dir() / game.slug
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in PSD_EXT
    )


def rotation_index(game: GameConfig) -> int:
    """How many thumbnails have already been generated for this game.

    DEPRECATED for template selection — the count-of-PNGs heuristic was
    unstable (it changed when a slug was deleted or re-rendered, so the
    rotation kept landing on the same template). Kept only for reporting.
    Use `next_template_index` for picking the template.
    """
    base = OUTPUTS_DIR / game.slug
    if not base.exists():
        return 0
    n = 0
    for vdir in base.iterdir():
        if not vdir.is_dir():
            continue
        if (vdir / f"{vdir.name}.png").exists():
            n += 1
    return n


def _rotation_state_path(game: GameConfig) -> Path:
    return OUTPUTS_DIR / game.slug / ".thumb_rotation.json"


def next_template_index(game: GameConfig, video_slug: str, n_templates: int) -> int:
    """Pick the next template in a true cyclic rotation, persisting state.

    State lives in `data/outputs/<game>/.thumb_rotation.json`:
        {"last_index": N, "by_slug": {"<slug>": idx, ...}}

    Rules:
      * A brand-new slug advances to (last_index + 1) % n_templates and
        records the choice — so consecutive videos cycle through every
        .psd before repeating.
      * Re-rendering an existing slug REUSES its recorded index, so a
        re-run (e.g. to fix the text) is idempotent and never jumps to a
        different template.

    Unlike the old count-of-PNGs approach this survives slug deletion and
    re-renders, which is what made it always land on the same template.
    """
    if n_templates <= 0:
        return 0
    path = _rotation_state_path(game)
    state: dict = {"last_index": -1, "by_slug": {}}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
        except Exception:  # noqa: BLE001 — corrupt state -> start over
            pass
    by_slug = state.get("by_slug") or {}
    if video_slug in by_slug:
        return int(by_slug[video_slug]) % n_templates
    nxt = (int(state.get("last_index", -1)) + 1) % n_templates
    by_slug[video_slug] = nxt
    state["by_slug"] = by_slug
    state["last_index"] = nxt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return nxt


def split_thumbnail_copy(text: str, strategy: str = "first_space") -> tuple[str, str]:
    """Split metadata.thumbnail_copy into (top, bottom) for the 2 SOs.

    Channel style is two short words ("BEGINNER GUIDE", "NEW DUNGEON"), so
    the default splits at the first space.
    """
    text = (text or "").strip()
    if not text:
        return ("", "")
    if strategy == "newline" and "\n" in text:
        head, _, tail = text.partition("\n")
        return (head.strip(), tail.strip())
    head, sep, tail = text.partition(" ")
    return ((head.strip(), tail.strip()) if sep else (head, ""))


# --------------------------------------------------------------------------- #
# COM bridge
# --------------------------------------------------------------------------- #
def _ps2021_running() -> bool:
    """True iff Photoshop 2021 (specifically) is up. The user also has PS
    2024 installed; matching on the EXE path distinguishes them."""
    target = str(photoshop_exe()).lower()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='Photoshop.exe'\" "
             "| Select-Object -ExpandProperty ExecutablePath"],
            capture_output=True, text=True, timeout=10,
        )
        return target in out.stdout.lower()
    except Exception:
        return False


def _wait_ready(ps, timeout_s: int = 90) -> None:
    """COM Dispatch returns before Photoshop is fully ready to accept
    DoJavaScript (the Home screen is loading). Poll a cheap property until
    it responds — that's our 'app is responsive' signal."""
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            _ = ps.Version            # any read confirms the bridge is up
            return
        except Exception as e:        # noqa: BLE001
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Photoshop COM not responsive ({last_err})")


def _get_photoshop():
    """Connect (booting PS 2021 if needed) via COM. Uses the VERSIONED
    ProgID so the bridge attaches to 2021, not the also-installed 2024."""
    import win32com.client            # Windows-only, declared in pyproject
    prog_id = photoshop_progid()

    if _ps2021_running():
        ps = win32com.client.Dispatch(prog_id)
        _wait_ready(ps)
        return ps

    exe = photoshop_exe()
    if not exe.exists():
        raise FileNotFoundError(
            f"Photoshop 2021 not found at {exe}. Set PHOTOSHOP_EXE to override."
        )
    subprocess.Popen([str(exe)])

    last_err: Exception | None = None
    for _ in range(120):
        if _ps2021_running():
            try:
                ps = win32com.client.Dispatch(prog_id)
                _wait_ready(ps)
                return ps
            except Exception as e:   # noqa: BLE001
                last_err = e
        time.sleep(1)
    raise RuntimeError(
        f"Photoshop 2021 didn't come up in time (last error: {last_err}). "
        f"Open it manually once and retry."
    )


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
def render_thumbnail(
    game: GameConfig,
    video_slug: str,
    *,
    top: str | None = None,
    bottom: str | None = None,
    template_index: int | None = None,
    output_path: Path | None = None,
) -> Path:
    """Render <slug>.png. Returns its path.

    If top/bottom are not given, reads metadata.thumbnail_copy from
    data/outputs/<game>/<slug>/metadata.json and splits per the game's
    split_strategy.
    """
    pt = game.photoshop_template
    templates = discover_templates(game)
    if not templates:
        raise FileNotFoundError(
            f"no .psd templates under {photoshop_templates_dir() / game.slug}"
        )
    idx = (
        template_index
        if template_index is not None
        else next_template_index(game, video_slug, len(templates))
    )
    idx = max(0, min(idx, len(templates) - 1))
    template = templates[idx]

    if top is None or bottom is None:
        from ..metadata.generator import VideoMetadata
        meta_path = OUTPUTS_DIR / game.slug / video_slug / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(
                f"missing {meta_path} - run "
                f"'yta metadata {game.slug} {video_slug}' first, "
                f"or pass --top/--bottom to render-thumb."
            )
        m = VideoMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
        if not m.candidates:
            raise ValueError("metadata has no title candidates")
        t_top, t_bot = split_thumbnail_copy(m.candidates[0].thumbnail_copy,
                                            pt.split_strategy)
        if top is None:
            top = t_top
        if bottom is None:
            bottom = t_bot

    out = output_path or (OUTPUTS_DIR / game.slug / video_slug / f"{video_slug}.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    jsx_lib = JSX_FILE.read_text(encoding="utf-8")
    args = (
        f"{json.dumps(str(template))}, "
        f"{json.dumps(top)}, "
        f"{json.dumps(bottom)}, "
        f"{json.dumps(str(out))}"
    )
    # The JSX ships with a placeholder YTA_PS_LOG path; override it with THIS
    # machine's tmp log path so the log lands where we tail it (portable
    # across clone locations / usernames).
    log = TMP_DIR / "yta_photoshop.log"
    log_override = f"YTA_PS_LOG = {json.dumps(str(log).replace(chr(92), '/'))};"
    # Photoshop accepts a .jsx as a command-line argument and runs it after
    # boot. We write the full call to a .jsx file and launch PS 2021 with
    # it -- avoids COM entirely (which silently dropped DoJavaScript on
    # this machine). If PS is already running, the .exe still kicks the
    # script through to the existing instance via standard file open.
    script = f"{jsx_lib}\n{log_override}\nytaRenderThumb({args});\n"
    jsx_call = TMP_DIR / "yta_thumb_call.jsx"
    jsx_call.parent.mkdir(parents=True, exist_ok=True)
    jsx_call.write_text(script, encoding="utf-8")

    # Pre-clear log + output so we only ever see THIS run.
    log.unlink(missing_ok=True)
    out.unlink(missing_ok=True)

    _suppress_script_warning()       # idempotent; effective after PS restart

    exe = photoshop_exe()
    if not exe.exists():
        raise FileNotFoundError(
            f"Photoshop 2021 not found at {exe}. Set PHOTOSHOP_EXE to override."
        )
    subprocess.Popen([str(exe), str(jsx_call)])

    # Poll for the PNG with stable-size detection.
    deadline = time.time() + 180
    last_size = 0
    stable_since = time.time()
    while time.time() < deadline:
        if out.exists():
            sz = out.stat().st_size
            if sz == last_size and sz > 0:
                if time.time() - stable_since > 3:
                    return out
            else:
                last_size = sz
                stable_since = time.time()
        time.sleep(2)
    tail = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(no log)"
    raise RuntimeError(
        "Photoshop didn't produce the PNG in time.\n"
        f"--- yta_photoshop.log tail ---\n{tail[-2000:]}"
    )


def render(*, thumbnail_copy: str, featured_image: Path, template_path: Path,
           output_png: Path) -> Path:
    """Legacy entrypoint kept for the original stub signature. Ignores
    `featured_image` (the new model edits text-only Smart Objects)."""
    top, bottom = split_thumbnail_copy(thumbnail_copy)
    jsx_lib = JSX_FILE.read_text(encoding="utf-8")
    args = (
        f"{json.dumps(str(template_path))}, "
        f"{json.dumps(top)}, "
        f"{json.dumps(bottom)}, "
        f"{json.dumps(str(output_png))}"
    )
    _get_photoshop().DoJavaScript(f"{jsx_lib}\nytaRenderThumb({args});")
    return output_png
