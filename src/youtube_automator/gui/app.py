"""YTA desktop app — Tkinter wrapper over the CLI.

Two tabs (LoM / LoE). Each tab shows the per-video slugs detected under
data/outputs/<game>/, with their state (metadata? mp4? uploaded?), and
runs the production pipeline for the selected rows:

    cut  ->  render-video --auto-render  ->  watch-and-upload

The heavy work is spawned in a background thread so the UI stays
responsive; thread output is streamed to a log pane on the main loop via
a queue.

Entry point installed by pyproject.toml as ``yta-gui``.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Iterable

from ..config import GameConfig, get_game, get_games
from ..paths import OUTPUTS_DIR, recordings_root


# --------------------------------------------------------------------------- #
# Background worker — runs CLI commands and streams output back to the UI.
# --------------------------------------------------------------------------- #
class Worker(threading.Thread):
    """Runs a sequence of CLI commands; each line of output is queued for UI."""

    def __init__(self, commands: list[list[str]], out_q: "queue.Queue[str]") -> None:
        super().__init__(daemon=True)
        self.commands = commands
        self.out_q = out_q

    def run(self) -> None:
        yta = Path(sys.executable).parent / "yta.exe"
        for cmd in self.commands:
            full = [str(yta), *cmd]
            self.out_q.put(f"\n$ {' '.join(full)}\n")
            try:
                proc = subprocess.Popen(
                    full,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as e:
                self.out_q.put(f"  spawn error: {e}\n")
                continue
            assert proc.stdout is not None
            for line in proc.stdout:
                self.out_q.put(line)
            rc = proc.wait()
            self.out_q.put(f"  (exit {rc})\n")
            if rc != 0:
                self.out_q.put("  STOP: previous command failed.\n")
                break
        self.out_q.put("__DONE__\n")


# --------------------------------------------------------------------------- #
# Per-game tab
# --------------------------------------------------------------------------- #
class GameTab(ttk.Frame):
    COLS = ("slug", "metadata", "mp4", "uploaded")

    def __init__(self, parent: tk.Widget, game: GameConfig, app: "App") -> None:
        super().__init__(parent)
        self.game = game
        self.app = app
        self.log = app.log
        self._build_widgets()
        self.refresh()

    def _build_widgets(self) -> None:
        # Pack order matters in Tk: claim the TOP bar and BOTTOM action bar
        # FIRST so the Treeview in the middle never pushes them off-screen.
        bar = ttk.Frame(self); bar.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(bar, text=f"Vídeos detectados para {self.game.display_name}:").pack(side="left")
        ttk.Button(bar, text="Refrescar", command=self.refresh).pack(side="right")

        actions = ttk.Frame(self); actions.pack(side="bottom", fill="x", padx=8, pady=8)
        ttk.Label(
            actions,
            text=f"Fragments root: {recordings_root() / self.game.slug}",
            foreground="#888",
        ).pack(side="left")
        ttk.Button(actions, text="Cut + Render", command=self.do_cut_render).pack(side="right", padx=4)
        ttk.Button(actions, text="Upload pendientes", command=self.do_upload).pack(side="right", padx=4)
        ttk.Button(actions, text="Pipeline completo", command=self.do_full).pack(side="right", padx=4)

        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings",
                                 selectmode="extended", height=8)
        self.tree.heading("slug", text="Video slug")
        self.tree.heading("metadata", text="metadata.json")
        self.tree.heading("mp4", text="MP4 rendered")
        self.tree.heading("uploaded", text="uploaded")
        self.tree.column("slug", width=240, anchor="w")
        self.tree.column("metadata", width=120, anchor="center")
        self.tree.column("mp4", width=120, anchor="center")
        self.tree.column("uploaded", width=120, anchor="center")
        self.tree.pack(side="top", fill="both", expand=True, padx=8)

    def refresh(self) -> None:
        for r in self.tree.get_children():
            self.tree.delete(r)
        base = OUTPUTS_DIR / self.game.slug
        if not base.exists():
            return
        for vdir in sorted(d for d in base.iterdir() if d.is_dir()):
            slug = vdir.name
            self.tree.insert("", "end", iid=slug, values=(
                slug,
                "yes" if (vdir / "metadata.json").exists() else "no",
                "yes" if (vdir / f"{slug}.mp4").exists() else "no",
                "yes" if (vdir / "uploaded.json").exists() else "no",
            ))

    def selected_slugs(self) -> list[str]:
        return list(self.tree.selection())

    def _frag_dir(self, slug: str) -> Path:
        return recordings_root() / self.game.slug / slug

    # ---- pipeline actions ------------------------------------------------- #
    def do_cut_render(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona uno o más slugs primero.")
            return
        cmds: list[list[str]] = []
        for slug in slugs:
            frag = self._frag_dir(slug)
            if not frag.exists():
                self.log(f"⚠ {slug}: missing fragments folder {frag}\n")
                continue
            cmds.append(["cut", self.game.slug, slug, "--fragments-dir", str(frag)])
            cmds.append(["render-video", self.game.slug, slug, "--auto-render"])
        if cmds:
            self.log(f"\n=== cut+render for {len(slugs)} slug(s) ===\n")
            self.app.run(cmds, on_done=self.refresh)

    def do_upload(self) -> None:
        cmds = [["watch-and-upload", self.game.slug, "--once"]]
        self.log(f"\n=== upload pendientes for {self.game.display_name} ===\n")
        self.app.run(cmds, on_done=self.refresh)

    def do_full(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona uno o más slugs primero.")
            return
        cmds: list[list[str]] = []
        for slug in slugs:
            frag = self._frag_dir(slug)
            if frag.exists():
                cmds.append(["cut", self.game.slug, slug, "--fragments-dir", str(frag)])
                cmds.append(["render-video", self.game.slug, slug, "--auto-render"])
        cmds.append(["watch-and-upload", self.game.slug, "--once"])
        self.log(f"\n=== pipeline completo for {len(slugs)} slug(s) ===\n")
        self.app.run(cmds, on_done=self.refresh)


# --------------------------------------------------------------------------- #
# Top-level window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YTA — YoutubeAutomator")
        self.geometry("1000x680")
        self._q: queue.Queue[str] = queue.Queue()
        self._worker: Worker | None = None
        self._on_done = None
        self._build_widgets()
        self.after(100, self._drain)

    def _build_widgets(self) -> None:
        # Vertical paned window so the user can drag the divider between the
        # notebook (slug tables) and the log -- both grow with the window.
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=6)

        nb = ttk.Notebook(paned)
        for game in get_games().values():
            tab = GameTab(nb, game, self)
            nb.add(tab, text=game.display_name)
        paned.add(nb, weight=3)

        log_frame = ttk.Frame(paned)
        ttk.Label(log_frame, text="Log:").pack(anchor="w")
        log_inner = ttk.Frame(log_frame)
        log_inner.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(log_inner, orient="vertical")
        sb.pack(side="right", fill="y")
        self.log_w = tk.Text(
            log_inner, bg="#111", fg="#ddd", font=("Consolas", 10),
            state="disabled", wrap="word", yscrollcommand=sb.set,
        )
        self.log_w.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.log_w.yview)
        paned.add(log_frame, weight=2)

    def log(self, msg: str) -> None:
        self.log_w.configure(state="normal")
        self.log_w.insert("end", msg)
        self.log_w.see("end")
        self.log_w.configure(state="disabled")

    def run(self, commands: list[list[str]], *, on_done=None) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Ocupado", "Ya hay un pipeline corriendo. Espera a que termine.")
            return
        self._on_done = on_done
        self._worker = Worker(commands, self._q)
        self._worker.start()

    def _drain(self) -> None:
        try:
            while True:
                line = self._q.get_nowait()
                if line == "__DONE__\n":
                    if self._on_done:
                        self._on_done()
                        self._on_done = None
                    continue
                self.log(line)
        except queue.Empty:
            pass
        self.after(150, self._drain)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
