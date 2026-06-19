"""Sincroniza el historial de conversaciones de Claude (y la memoria) desde la
carpeta del worktree del disco viejo a la carpeta del proyecto en
C:\\YoutubeAutomator, y reancla la ruta interna (cwd) de cada conversacion al
nuevo repo, para que aparezcan asociadas a C:\\YoutubeAutomator.

Doble clic en el acceso directo del escritorio (lanza pythonw, sin terminal).
Muestra una ventanita con el resultado. Con --quiet imprime por consola.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
SRC = PROJECTS / "F--Users-Usuario-Downloads-YoutubeAutomator-YoutubeAutomator--claude-worktrees-gallant-euler-e23186"
MEM_SRC = PROJECTS / "F--Users-Usuario-Downloads-YoutubeAutomator-YoutubeAutomator" / "memory"
DST = PROJECTS / "C--YoutubeAutomator"

# Cualquier ruta vieja del repo (o su worktree), con cualquier letra de unidad
# (en el PC viejo era C:, en el disco externo es F:) y con barras normales o
# escapadas, se reancla al repo nuevo. El sufijo del worktree es opcional y
# greedy, asi que repo y repo+worktree colapsan a la raiz del repo nuevo.
_OLD = re.compile(
    r"[A-Za-z]:[\\/]+Users[\\/]+Usuario[\\/]+Downloads[\\/]+YoutubeAutomator[\\/]+YoutubeAutomator"
    r"(?:[\\/]+\.claude[\\/]+worktrees[\\/]+gallant-euler-e23186)?"
)
_NEW = r"C:\\YoutubeAutomator"  # backslash escapado: valido dentro de cualquier string JSON


def _reanchor(text: str) -> str:
    return _OLD.sub(lambda _m: _NEW, text)


def main() -> None:
    quiet = "--quiet" in sys.argv
    msgs: list[str] = []

    if SRC.exists():
        DST.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in SRC.glob("*.jsonl"):
            (DST / f.name).write_text(_reanchor(f.read_text(encoding="utf-8")), encoding="utf-8")
            copied += 1
        msgs.append(f"{copied} conversaciones sincronizadas y reancladas a C:.")
    else:
        msgs.append("AVISO: no se encontro la carpeta de conversaciones del worktree.")

    if MEM_SRC.exists() and any(MEM_SRC.iterdir()):
        shutil.copytree(MEM_SRC, DST / "memory", dirs_exist_ok=True)
        msgs.append("Memoria sincronizada.")

    text = "\n".join(msgs) + f"\n\nDestino:\n{DST}"

    if quiet:
        print(text)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Sincronizar chats YTA", text)
        root.destroy()
    except Exception:  # noqa: BLE001 — sin display: cae a consola
        print(text)


if __name__ == "__main__":
    main()
