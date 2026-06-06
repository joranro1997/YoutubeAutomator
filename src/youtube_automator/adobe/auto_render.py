"""Auto-render via the Premiere -> Adobe Media Encoder bridge.

Premiere 2020 has no CLI flag to run a .jsx; the Startup Scripts mechanism
doesn't fire on this setup, and QE DOM crashes. The robust, validated path:

  1. Python writes the render queue to data/tmp/yta_render_jobs.json.
  2. The user runs scripts/jsx/yta_encoder.jsx ONCE via the VS Code
     ExtendScript Debugger (F5). That .jsx opens each .prproj, queues it
     to AME via app.encoder.encodeSequence, then startBatch().
  3. AME renders asynchronously while Python polls for the output MP4s.

Requires Adobe Media Encoder 2020 (same generation as Premiere 2020;
2021+ doesn't bridge to Premiere 14.x).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..paths import REPO_ROOT, TMP_DIR

CEP_SRC = REPO_ROOT / "scripts" / "cep" / "YTA"
CEP_VERSIONS = ("9", "10", "11", "12")   # CSXS keys to enable PlayerDebugMode in

DEFAULT_PRESET = (
    r"C:\Users\Usuario\Documents\Adobe\Adobe Media Encoder\14.0\Presets\yta_render.epr"
)
DEFAULT_PREMIERE_EXE = r"C:\Program Files\Adobe\Adobe Premiere Pro 2020\Adobe Premiere Pro.exe"
DEFAULT_AME_EXE = r"C:\Program Files\Adobe\Adobe Media Encoder 2020\Adobe Media Encoder.exe"
ENCODER_JSX = REPO_ROOT / "scripts" / "jsx" / "yta_encoder.jsx"
QUEUE_PATH = TMP_DIR / "yta_render_jobs.json"


def _media_cache_dirs() -> list[Path]:
    """Adobe's shared media cache (Premiere + AME). Windows-only; [] elsewhere."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "Adobe" / "Common"
    return [base / "Media Cache Files", base / "Media Cache"]


def adobe_running() -> bool:
    """True if Premiere or AME is up (its cache is in use — don't delete it)."""
    for img in ("Adobe Premiere Pro.exe", "Adobe Media Encoder.exe"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {img}", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            if img.lower() in out.stdout.lower():
                return True
        except Exception:  # noqa: BLE001 — tasklist absent (non-Windows) -> assume not running
            pass
    return False


def clear_media_cache() -> dict:
    """Delete Adobe's media cache to reclaim disk before a render.

    Returns {"ran": bool, "reason": str, "freed_bytes": int}. Skips (ran=
    False) when Premiere/AME are running, since the cache is then in use.
    Adobe regenerates the cache on demand, so deleting it is safe.
    """
    if adobe_running():
        return {"ran": False, "reason": "adobe_running", "freed_bytes": 0}
    freed = 0
    for d in _media_cache_dirs():
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                try:
                    sz = p.stat().st_size
                    p.unlink()
                    freed += sz
                except OSError:
                    pass
        # prune now-empty subdirectories (deepest first)
        for p in sorted(d.rglob("*"), key=lambda x: len(str(x)), reverse=True):
            if p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
    return {"ran": True, "reason": "", "freed_bytes": freed}


def render_preset() -> Path:
    return Path(os.getenv("YTA_RENDER_PRESET", DEFAULT_PRESET))


def premiere_exe() -> Path:
    return Path(os.getenv("PREMIERE_EXE", DEFAULT_PREMIERE_EXE))


def ame_exe() -> Path:
    return Path(os.getenv("AME_EXE", DEFAULT_AME_EXE))


def _launch(exe: Path) -> subprocess.Popen | None:
    if not exe.exists():
        return None
    return subprocess.Popen([str(exe)])


def cep_extensions_dir() -> Path:
    """Per-user CEP extensions root (no admin)."""
    override = os.getenv("CEP_EXTENSIONS_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "Adobe" / "CEP" / "extensions"


def install_cep_panel() -> dict:
    """Install scripts/cep/YTA/ to %APPDATA%/Adobe/CEP/extensions/YTA/ and
    flip PlayerDebugMode=1 in HKCU\\Software\\Adobe\\CSXS.N for the CEP
    versions Premiere may use. Returns a summary of what was done.
    """
    if not CEP_SRC.exists():
        raise FileNotFoundError(f"CEP source missing: {CEP_SRC}")

    dst = cep_extensions_dir() / "YTA"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(CEP_SRC, dst)

    # PlayerDebugMode lets Premiere load this unsigned extension.
    import winreg  # Windows-only; auto_render is Windows-only anyway.
    enabled = []
    for ver in CEP_VERSIONS:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Adobe\CSXS.{ver}")
            winreg.SetValueEx(key, "PlayerDebugMode", 0, winreg.REG_SZ, "1")
            winreg.CloseKey(key)
            enabled.append(ver)
        except OSError as e:
            enabled.append(f"{ver}(err:{e})")
    return {"installed_at": str(dst), "csxs_versions_enabled": enabled}


def write_queue(jobs: list[dict]) -> Path:
    """Write the render queue. The .jsx reads this via ES3 eval()."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    norm = [
        {k: (v.replace("\\", "/") if isinstance(v, str) else v) for k, v in j.items()}
        for j in jobs
    ]
    QUEUE_PATH.write_text(json.dumps(norm, indent=2), encoding="utf-8")
    return QUEUE_PATH


def run_jobs(
    jobs: list[dict],
    *,
    timeout_s: int = 3600 * 6,
    poll_s: float = 5.0,
    stable_s: float = 8.0,
    force: bool = False,
) -> dict:
    """Queue jobs for AME and wait for the MP4 outputs.

    The user must F5 scripts/jsx/yta_encoder.jsx in VS Code once after this
    call writes the queue (Premiere has no CLI script trigger). Returns a
    summary {queued, done, missing, elapsed_s}.
    """
    if not ENCODER_JSX.exists():
        raise FileNotFoundError(f"missing {ENCODER_JSX}")

    # Safety: never silently overwrite a rendered MP4 that might still be
    # waiting to upload. Skip those jobs unless --force was passed.
    pending: list[dict] = []
    skipped: list[str] = []
    for j in jobs:
        out = Path(j["output"])
        if out.exists() and not force:
            skipped.append(str(out))
            continue
        if out.exists() and force:
            try:
                out.unlink()
            except OSError:
                pass
        pending.append(j)
    if skipped:
        print(
            f"\n[skip] {len(skipped)} job(s) whose MP4 already exists "
            "(use --force to re-render):"
        )
        for s in skipped:
            print(f"    {s}")
    if not pending:
        return {"queued": 0, "done": [], "missing": [], "skipped": skipped, "elapsed_s": 0.0}

    write_queue(pending)
    jobs = pending  # everything below operates on the filtered list

    # Launch both Adobe apps. AME must be RUNNING when the worker calls
    # encodeSequence (or the job is silently dropped on 2020). Launch
    # Premiere WITH the first job's .prproj so we skip the Welcome dialog
    # (welcome blocks the CEP panel from auto-opening).
    ame = _launch(ame_exe())
    pr_exe = premiere_exe()
    pr = subprocess.Popen([str(pr_exe), jobs[0]["project"]]) if pr_exe.exists() else None
    pids = []
    if ame is None:
        print(f"\n[warn] AME not found at {ame_exe()}. Set AME_EXE or open AME manually.")
    else:
        pids.append(f"AME pid {ame.pid}")
    if pr is None:
        print(f"[warn] Premiere not found at {premiere_exe()}. Set PREMIERE_EXE or open Premiere manually.")
    else:
        pids.append(f"Premiere pid {pr.pid}")
    if pids:
        print(f"\n[launched] {', '.join(pids)}.")

    print(
        f"\n[queue] {len(jobs)} job(s) written.\n"
        "  If the YTA Worker CEP panel is installed (yta install-cep), it will\n"
        "  pick up the queue within ~15s -- no further action needed.\n"
        "  Fallback (no CEP): open scripts/jsx/yta_encoder.jsx in VS Code and F5.\n"
    )

    outputs = [Path(j["output"]) for j in jobs]
    start = time.time()
    last_sizes: dict[Path, int] = {}
    stable_since: dict[Path, float] = {}
    done: set[Path] = set()
    queue_was_consumed = False

    while time.time() - start < timeout_s:
        # The .jsx removes the queue file once it hands jobs to AME.
        if not QUEUE_PATH.exists():
            queue_was_consumed = True
        for p in outputs:
            if p in done:
                continue
            if p.exists():
                size = p.stat().st_size
                if last_sizes.get(p) == size and size > 0:
                    if time.time() - stable_since.get(p, time.time()) >= stable_s:
                        done.add(p)
                else:
                    last_sizes[p] = size
                    stable_since[p] = time.time()
        if len(done) == len(outputs):
            break
        time.sleep(poll_s)

    return {
        "queued": len(jobs),
        "done": [str(p) for p in done],
        "missing": [str(p) for p in outputs if p not in done],
        "queue_consumed": queue_was_consumed,
        "elapsed_s": round(time.time() - start, 1),
    }
