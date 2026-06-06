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
from tkinter import messagebox, simpledialog, ttk
from typing import Iterable

from ..config import GameConfig, get_game, get_games
from ..paths import MIN_RENDER_FREE_GB, OUTPUTS_DIR, free_space_gb, recordings_root


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
        import os

        yta = Path(sys.executable).parent / "yta.exe"
        # Force UTF-8 in the child so LLM-generated emoji / curly quotes
        # don't kill rich.print on the cp1252 default pipe.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
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
                    env=env,
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
    COLS = ("slug", "script", "metadata", "mp4", "thumb", "uploaded")

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
        ttk.Button(bar, text="Refrescar", command=self.refresh).pack(side="right", padx=4)
        ttk.Button(bar, text="Topics SEO", command=self.open_topics).pack(side="right", padx=4)

        actions = ttk.Frame(self); actions.pack(side="bottom", fill="x", padx=8, pady=8)
        ttk.Label(
            actions,
            text=f"Fragments root: {recordings_root() / self.game.slug}",
            foreground="#888",
        ).pack(side="left")
        ttk.Button(actions, text="Eliminar", command=self.do_delete).pack(side="right", padx=4)
        ttk.Button(actions, text="Ver script", command=self.do_view_script).pack(side="right", padx=4)
        ttk.Button(actions, text="Cut + Render", command=self.do_cut_render).pack(side="right", padx=4)
        ttk.Button(actions, text="Thumbnail", command=self.do_thumb).pack(side="right", padx=4)
        ttk.Button(actions, text="Upload pendientes", command=self.do_upload).pack(side="right", padx=4)
        ttk.Button(actions, text="Pipeline completo", command=self.do_full).pack(side="right", padx=4)

        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings",
                                 selectmode="extended", height=8)
        self.tree.heading("slug", text="Video slug")
        self.tree.heading("script", text="script.json")
        self.tree.heading("metadata", text="metadata.json")
        self.tree.heading("mp4", text="MP4 rendered")
        self.tree.heading("thumb", text="Thumbnail")
        self.tree.heading("uploaded", text="uploaded")
        self.tree.column("slug", width=220, anchor="w")
        self.tree.column("script", width=100, anchor="center")
        self.tree.column("metadata", width=110, anchor="center")
        self.tree.column("mp4", width=110, anchor="center")
        self.tree.column("thumb", width=110, anchor="center")
        self.tree.column("uploaded", width=100, anchor="center")
        self.tree.bind("<Double-1>", self._on_double_click)
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
                "yes" if (vdir / "script.json").exists() else "no",
                "yes" if (vdir / "metadata.json").exists() else "no",
                "yes" if (vdir / f"{slug}.mp4").exists() else "no",
                "yes" if (vdir / f"{slug}.png").exists() else "no",
                "yes" if (vdir / "uploaded.json").exists() else "no",
            ))

    def selected_slugs(self) -> list[str]:
        return list(self.tree.selection())

    def _frag_dir(self, slug: str) -> Path:
        return recordings_root() / self.game.slug / slug

    def _disk_ok(self) -> bool:
        """Warn (and let the user bail) when the output drive is too full to
        render. A render that runs out of space dies mid-export in AME with a
        cryptic error after wasting several minutes."""
        free = free_space_gb(OUTPUTS_DIR / self.game.slug)
        if free >= MIN_RENDER_FREE_GB:
            return True
        return messagebox.askyesno(
            "Poco espacio en disco",
            f"Solo quedan {free:.1f} GB libres (recomendado >= {MIN_RENDER_FREE_GB:.0f} GB "
            f"para renderizar).\n\nUn render que se quede sin espacio falla a medias en AME.\n\n"
            f"Si continúas, se limpiará automáticamente el Media Cache de Adobe para "
            f"recuperar espacio antes de renderizar (seguro: Adobe lo regenera).\n\n"
            f"¿Continuar?",
            icon="warning",
            default="no",
        )

    # ---- pipeline actions ------------------------------------------------- #
    def do_cut_render(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona uno o más slugs primero.")
            return
        if not self._disk_ok():
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

    def do_thumb(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona uno o más slugs primero.")
            return
        cmds: list[list[str]] = []
        for slug in slugs:
            meta = OUTPUTS_DIR / self.game.slug / slug / "metadata.json"
            if meta.exists():
                cmds.append(["render-thumb", self.game.slug, slug])
                continue
            # No metadata: prompt for manual top/bottom text.
            top = simpledialog.askstring(
                "Texto thumbnail",
                f"{slug}: no hay metadata.json todavía.\n"
                f"Texto SUPERIOR (1ª palabra):",
                parent=self.app,
            )
            if top is None:
                self.log(f"  [skip] {slug}: cancelled\n")
                continue
            bottom = simpledialog.askstring(
                "Texto thumbnail",
                f"{slug}: texto INFERIOR (resto):",
                parent=self.app,
            )
            if bottom is None:
                self.log(f"  [skip] {slug}: cancelled\n")
                continue
            cmds.append([
                "render-thumb", self.game.slug, slug,
                "--top", top, "--bottom", bottom,
            ])
        if not cmds:
            return
        self.log(f"\n=== render-thumb for {len(cmds)} slug(s) ===\n")
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
        if not self._disk_ok():
            return
        cmds: list[list[str]] = []
        for slug in slugs:
            frag = self._frag_dir(slug)
            if frag.exists():
                cmds.append(["cut", self.game.slug, slug, "--fragments-dir", str(frag)])
                cmds.append(["render-video", self.game.slug, slug, "--auto-render"])
            cmds.append(["render-thumb", self.game.slug, slug])
        cmds.append(["watch-and-upload", self.game.slug, "--once"])
        self.log(f"\n=== pipeline completo for {len(slugs)} slug(s) ===\n")
        self.app.run(cmds, on_done=self.refresh)

    # ---- destructive: delete slug ---------------------------------------- #
    def do_delete(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona uno o más slugs primero.")
            return
        # Build a preview of what will go for each slug so the user sees
        # exactly the bytes/folders we're about to wipe before confirming.
        import shutil

        lines: list[str] = []
        targets: list[tuple[str, list[Path]]] = []
        for slug in slugs:
            paths = [
                OUTPUTS_DIR / self.game.slug / slug,
                self._frag_dir(slug),
            ]
            existing = [p for p in paths if p.exists()]
            if not existing:
                continue
            targets.append((slug, existing))
            lines.append(f"\n• {slug}")
            for p in existing:
                lines.append(f"    {p}")
        if not targets:
            messagebox.showinfo("nada que borrar", "Ninguno de los slugs tiene carpetas en disco.")
            return
        confirm = messagebox.askyesno(
            "Eliminar definitivamente",
            "Se borrarán estas carpetas (outputs + recordings) sin papelera:\n"
            + "\n".join(lines)
            + "\n\nEl vídeo en YouTube NO se toca — solo los datos locales.\n"
            "¿Continuar?",
            icon="warning",
            default="no",
        )
        if not confirm:
            return
        for slug, paths in targets:
            for p in paths:
                try:
                    shutil.rmtree(p)
                    self.log(f"  removed {p}\n")
                except OSError as e:
                    self.log(f"  ⚠ failed to remove {p}: {e}\n")
        self.refresh()
        self.log(f"=== deleted {len(targets)} slug(s) ===\n")

    # ---- script viewer ---------------------------------------------------- #
    def _on_double_click(self, _event) -> None:
        col = self.tree.identify_column(_event.x)
        # Double-click on the "script" column (#2) opens the viewer.
        if col == "#2":
            self.do_view_script()

    def do_view_script(self) -> None:
        slugs = self.selected_slugs()
        if not slugs:
            messagebox.showinfo("nada seleccionado", "Selecciona un slug primero.")
            return
        for slug in slugs:
            path = OUTPUTS_DIR / self.game.slug / slug / "script.json"
            if not path.exists():
                self.log(f"  [skip] {slug}: no script.json — run 'Crear script + metadata' first\n")
                continue
            ScriptViewer(self.app, self.game, slug, path)

    # ---- topics SEO dialog ----------------------------------------------- #
    def open_topics(self) -> None:
        TopicsWindow(self.app, self.game, self.log, on_change=self.refresh)


# --------------------------------------------------------------------------- #
# Topics SEO dialog
# --------------------------------------------------------------------------- #
class TopicsWindow(tk.Toplevel):
    """A modeless dialog showing the latest SEO topics for one game.

    Lets the user inspect topic candidates and, per topic, type a video
    slug and trigger `yta script <slug> --topic N` + `yta metadata <slug>`.
    The Refrescar button regenerates research+topics from scratch.
    """

    def __init__(self, app: "App", game: GameConfig, log_fn, *, on_change) -> None:
        super().__init__(app)
        self.app = app
        self.game = game
        self.log = log_fn
        self.on_change = on_change
        self.title(f"Topics SEO — {game.display_name}")
        self.geometry("900x540")
        self._build()
        self.refresh()

    def _topics_path(self) -> Path:
        return OUTPUTS_DIR / self.game.slug / "topics_latest.json"

    def _build(self) -> None:
        top = ttk.Frame(self); top.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(top, text=f"Topics for {self.game.display_name}:").pack(side="left")
        ttk.Button(top, text="Generar nuevos (research + topics)",
                   command=self.do_generate).pack(side="right", padx=4)
        ttk.Button(top, text="Refrescar lista", command=self.refresh).pack(side="right", padx=4)

        # Scrollable list of topic cards (one per topic), each with a slug
        # entry and an "Crear script + metadata" button.
        canvas_frame = ttk.Frame(self); canvas_frame.pack(fill="both", expand=True, padx=8)
        self._canvas = tk.Canvas(canvas_frame, borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))

    def refresh(self) -> None:
        for child in self._inner.winfo_children():
            child.destroy()
        path = self._topics_path()
        if not path.exists():
            ttk.Label(self._inner, foreground="#888",
                      text=f"No topics yet. Click 'Generar nuevos' to run research + topics.").pack(
                anchor="w", pady=20)
            return
        import json as _json
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            ttk.Label(self._inner, foreground="#c66", text=f"error: {e}").pack(pady=20)
            return
        for i, t in enumerate(data):
            self._add_card(i, t)

    def _add_card(self, i: int, t: dict) -> None:
        f = ttk.Frame(self._inner, padding=8, relief="groove")
        f.pack(fill="x", pady=4)
        head = ttk.Frame(f); head.pack(fill="x")
        ttk.Label(head, text=f"#{i}", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=str(t.get("title_hook", "")),
                  font=("Segoe UI", 10, "bold")).pack(side="left", anchor="w")
        ttk.Label(head, foreground="#888",
                  text=f"  appeal={t.get('appeal_score', '?')}  conv={t.get('conversion_score', '?')}"
                  ).pack(side="left")

        ang = t.get("angle") or ""
        rat = t.get("rationale") or ""
        if ang:
            ttk.Label(f, text=f"Angle: {ang}", foreground="#aaa", wraplength=820,
                      justify="left").pack(anchor="w")
        if rat:
            ttk.Label(f, text=f"Why: {rat}", foreground="#888", wraplength=820,
                      justify="left").pack(anchor="w")

        act = ttk.Frame(f); act.pack(fill="x", pady=(6, 0))
        ttk.Label(act, text="Video slug:").pack(side="left")
        slug_var = tk.StringVar()
        ttk.Entry(act, textvariable=slug_var, width=30).pack(side="left", padx=4)
        ttk.Button(
            act, text="Crear script + metadata",
            command=lambda i=i, v=slug_var: self._make_video(i, v.get().strip()),
        ).pack(side="left", padx=4)

    def _make_video(self, topic_index: int, slug: str) -> None:
        if not slug:
            messagebox.showinfo("falta slug", "Pon un slug para el vídeo (p.ej. stop-doing-X).")
            return
        cmds = [
            ["script", self.game.slug, slug, "--topic", str(topic_index)],
            ["metadata", self.game.slug, slug],
        ]
        self.log(f"\n=== script+metadata for topic #{topic_index} -> slug {slug!r} ===\n")
        self.app.run(cmds, on_done=self.on_change)

    def do_generate(self) -> None:
        cmds = [
            ["research", self.game.slug],
            ["topics", self.game.slug, "--n", "5"],
        ]
        self.log(f"\n=== research + topics for {self.game.display_name} ===\n")
        self.app.run(cmds, on_done=self.refresh)


# --------------------------------------------------------------------------- #
# Script viewer — read-only window per slug
# --------------------------------------------------------------------------- #
class ScriptViewer(tk.Toplevel):
    """Read-only formatted view of ``script.json`` for one slug.

    Shows the topic hook, total estimated duration, and each segment as a
    labelled block (kind + duration, narration text, shot notes, citations).
    """

    def __init__(self, app: "App", game: GameConfig, slug: str, path: Path) -> None:
        super().__init__(app)
        self.title(f"Script — {game.display_name} / {slug}")
        self.geometry("980x680")

        bar = ttk.Frame(self); bar.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(bar, text=str(path), foreground="#888").pack(side="left")
        ttk.Button(bar, text="Abrir en editor",
                   command=lambda: self._open_in_editor(path)).pack(side="right", padx=4)
        ttk.Button(bar, text="Copiar todo",
                   command=self._copy_all).pack(side="right", padx=4)

        body = ttk.Frame(self); body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        sb = ttk.Scrollbar(body, orient="vertical")
        sb.pack(side="right", fill="y")
        self.txt = tk.Text(
            body, bg="#1a1a1a", fg="#e0e0e0", font=("Segoe UI", 10),
            wrap="word", yscrollcommand=sb.set, padx=10, pady=8,
        )
        self.txt.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.txt.yview)

        # tags for styling
        self.txt.tag_configure("h1", font=("Segoe UI", 13, "bold"), foreground="#fff",
                               spacing1=6, spacing3=6)
        self.txt.tag_configure("h2", font=("Segoe UI", 11, "bold"), foreground="#7fd6ff",
                               spacing1=10, spacing3=4)
        self.txt.tag_configure("meta", foreground="#888")
        self.txt.tag_configure("notes", foreground="#aaa", lmargin1=20, lmargin2=20)

        self._render(path)
        self.txt.configure(state="disabled")

    def _render(self, path: Path) -> None:
        import json as _json

        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self.txt.insert("end", f"error reading {path}: {e}\n")
            return
        topic = data.get("topic") or {}
        segs = data.get("segments") or []
        total = sum(int(s.get("duration_s_estimate") or 0) for s in segs)

        self.txt.insert("end", f"{topic.get('title_hook', '(no title)')}\n", "h1")
        self.txt.insert(
            "end",
            f"appeal={topic.get('appeal_score', '?')}  "
            f"conversion={topic.get('conversion_score', '?')}  "
            f"total≈{total}s ({total // 60}m{total % 60:02d}s)  "
            f"segments={len(segs)}\n\n",
            "meta",
        )
        ang = topic.get("angle") or ""
        if ang:
            self.txt.insert("end", f"Angle: {ang}\n\n", "meta")

        for i, s in enumerate(segs):
            kind = s.get("kind", "?")
            dur = s.get("duration_s_estimate", "?")
            self.txt.insert("end", f"[{i:02d}] {kind}  ({dur}s)\n", "h2")
            text = (s.get("text") or "").strip()
            if text:
                self.txt.insert("end", text + "\n")
            shot = (s.get("shot_notes") or "").strip()
            if shot:
                self.txt.insert("end", f"Shot notes: {shot}\n", "notes")
            cites = s.get("citations") or []
            if cites:
                self.txt.insert("end", f"Citations: {', '.join(cites)}\n", "notes")
            self.txt.insert("end", "\n")

    def _copy_all(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.txt.get("1.0", "end-1c"))

    @staticmethod
    def _open_in_editor(path: Path) -> None:
        import os
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Top-level window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YTA — YoutubeAutomator")
        # Wide enough for the 6 action buttons (Pipeline / Upload / Thumb /
        # Cut+Render / Ver script / Eliminar) without clipping on first open.
        self.geometry("1280x720")
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
