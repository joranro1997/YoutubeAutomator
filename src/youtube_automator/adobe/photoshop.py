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


def split_thumbnail_copy(
    text: str, strategy: str = "first_space", max_words: int = 3
) -> tuple[str, str]:
    """Split metadata.thumbnail_copy into (top, bottom) for the 2 SOs.

    Channel style is a punchy hook word on top + a 1-2 word subject below
    ("FREE" / "NEW HEROES"). Thumbnails read best with very few BIG words, so
    we HARD-CAP the copy to ``max_words`` (default 3): the first word goes top,
    the rest (≤ max_words-1) go bottom. Fewer words also means the renderer
    shrinks the text less, so it stays large and fills the design box.
    """
    text = (text or "").strip()
    if not text:
        return ("", "")
    # Cap total words first so an over-long LLM copy can't unbalance the layout.
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
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

    _suppress_script_warning()       # idempotent; effective after PS restart
    exe = photoshop_exe()
    if not exe.exists():
        raise FileNotFoundError(
            f"Photoshop 2021 not found at {exe}. Set PHOTOSHOP_EXE to override."
        )

    # First render. If the text overflowed its Smart Object so badly that even
    # shrinking to the floor couldn't save it, shorten the offending side and
    # render again — up to a couple of times (the auto-fit handles every
    # realistic case; this is the documented last resort the user asked for).
    fit = _render_once(exe, template, top, bottom, out, pt)
    cur_top, cur_bottom = top, bottom
    for _ in range(2):
        if not (pt.autofit_text and _overflowing(fit)):
            break
        cur_top, cur_bottom, changed = _shorten_for_fit(cur_top, cur_bottom, fit)
        if not changed:           # nothing left to trim (e.g. a single long word)
            break
        fit = _render_once(exe, template, cur_top, cur_bottom, out, pt)
    return out


def _render_once(
    exe: Path, template: Path, top: str, bottom: str, out: Path, pt
) -> list[dict]:
    """One Photoshop pass: launch the .jsx, wait for the PNG, return the
    per-Smart-Object fit report (``[]`` if none was written)."""
    jsx_lib = JSX_FILE.read_text(encoding="utf-8")
    args = ", ".join([
        json.dumps(str(template)),
        json.dumps(top),
        json.dumps(bottom),
        json.dumps(str(out)),
        json.dumps(bool(pt.autofit_text)),
        json.dumps(float(pt.text_fit_margin)),
        json.dumps(float(pt.text_fit_min_scale)),
    ])
    # The JSX ships with placeholder YTA_PS_LOG / YTA_FIT_OUT paths; override
    # them with THIS machine's tmp paths so the log + fit sidecar land where we
    # read them (portable across clone locations / usernames).
    log = TMP_DIR / "yta_photoshop.log"
    fit_path = TMP_DIR / "yta_thumbfit.json"
    log_override = f"YTA_PS_LOG = {json.dumps(str(log).replace(chr(92), '/'))};"
    fit_override = f"YTA_FIT_OUT = {json.dumps(str(fit_path).replace(chr(92), '/'))};"
    # Photoshop accepts a .jsx as a command-line argument and runs it after
    # boot. We write the full call to a .jsx file and launch PS 2021 with
    # it -- avoids COM entirely (which silently dropped DoJavaScript on
    # this machine). If PS is already running, the .exe still kicks the
    # script through to the existing instance via standard file open.
    script = f"{jsx_lib}\n{log_override}\n{fit_override}\nytaRenderThumb({args});\n"
    jsx_call = TMP_DIR / "yta_thumb_call.jsx"
    jsx_call.parent.mkdir(parents=True, exist_ok=True)
    jsx_call.write_text(script, encoding="utf-8")

    # Pre-clear log + output + fit so we only ever see THIS run.
    log.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    fit_path.unlink(missing_ok=True)

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
                    return _read_fit(fit_path)
            else:
                last_size = sz
                stable_since = time.time()
        time.sleep(2)
    tail = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(no log)"
    raise RuntimeError(
        "Photoshop didn't produce the PNG in time.\n"
        f"--- yta_photoshop.log tail ---\n{tail[-2000:]}"
    )


def _read_fit(fit_path: Path) -> list[dict]:
    """Parse the JSX fit sidecar; tolerant of a missing / malformed file."""
    if not fit_path.exists():
        return []
    try:
        data = json.loads(fit_path.read_text(encoding="utf-8"))
        # Keep only dict entries so _overflowing / _shorten_for_fit / the CLI
        # report (all of which call .get) can't trip on a stray scalar.
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — best-effort; absence just disables the fallback
        return []


def _overflowing(fit: list[dict]) -> bool:
    """True if any Smart Object's text still overflows after auto-fit."""
    return any(bool(f.get("overflow")) for f in fit)


def _shorten_for_fit(top: str, bottom: str, fit: list[dict]) -> tuple[str, str, bool]:
    """Last resort when auto-fit hit its floor and the text STILL overflows:
    drop the trailing word from whichever side overflowed (multi-word only —
    a single word is left for the floor-shrink to handle rather than mangled).
    Returns (new_top, new_bottom, changed)."""
    by_role = {f.get("role"): f for f in fit}

    def drop_last(s: str) -> str:
        words = (s or "").split()
        return " ".join(words[:-1]) if len(words) > 1 else s

    new_top = drop_last(top) if by_role.get("top", {}).get("overflow") else top
    new_bottom = drop_last(bottom) if by_role.get("bottom", {}).get("overflow") else bottom
    return new_top, new_bottom, (new_top != top or new_bottom != bottom)


def last_thumbnail_fit() -> list[dict]:
    """Per-Smart-Object text-fit report from the most recent render_thumbnail
    call: ``[{role, text, shrunk, overflow, final_scale}, ...]`` (empty if the
    last render didn't write one). Lets the CLI flag clipped/shrunk text."""
    return _read_fit(TMP_DIR / "yta_thumbfit.json")


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
