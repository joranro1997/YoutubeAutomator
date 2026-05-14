"""Typer-based CLI: `yta <command>`.

Commands map 1:1 to pipeline stages so the user can drive each step manually
during the human-in-the-loop phases:

    yta research <game>         # run sources, write snapshot
    yta topics <game>            # propose N topic candidates
    yta script <game> --topic N  # generate a script for chosen topic
    yta metadata <game>          # generate title/desc/tags for an approved script
    yta render-video <game>      # (Windows) drive Premiere
    yta render-thumb <game>      # (Windows) drive Photoshop
    yta upload <game>            # upload to YouTube (privacy=private by default)
    yta ingest-transcripts ...   # one-off: build style corpus from past videos
"""

from __future__ import annotations

import typer

app = typer.Typer(help="YoutubeAutomator CLI")


@app.command()
def research(game: str) -> None:
    """Run all sources for GAME and write a research snapshot."""
    from .config import get_game
    from .research.aggregator import run

    g = get_game(game)
    path = run(g)
    typer.echo(f"wrote snapshot: {path}")


@app.command()
def topics(game: str, n: int = 5, no_style: bool = False) -> None:
    """Propose N topic candidates from the latest research snapshot."""
    from rich.console import Console
    from rich.table import Table

    from .config import get_game
    from .ideation.topic_generator import propose
    from .research.aggregator import latest_snapshot
    from .script.style_corpus import style_prompt

    g = get_game(game)
    items = latest_snapshot(g)
    if not items:
        typer.echo("no snapshot found — run `yta research` first", err=True)
        raise typer.Exit(1)

    excerpt = "" if no_style else style_prompt()
    candidates = propose(g, items, n=n, style_excerpt=excerpt)
    if not candidates:
        typer.echo("no candidates returned", err=True)
        raise typer.Exit(1)

    console = Console()
    table = Table(title=f"Topic candidates for {g.display_name}")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Title hook", style="bold")
    table.add_column("Appeal", justify="right")
    table.add_column("Conv.", justify="right")
    table.add_column("Angle")
    for i, c in enumerate(candidates):
        table.add_row(
            str(i),
            c.title_hook,
            str(c.appeal_score),
            str(c.conversion_score),
            c.angle,
        )
    console.print(table)
    for i, c in enumerate(candidates):
        console.print(f"\n[bold cyan]#{i}[/] [bold]{c.title_hook}[/]")
        console.print(f"  Why: {c.rationale}")
        if c.grounding_urls:
            console.print(f"  Sources: {', '.join(c.grounding_urls)}")


@app.command()
def script(game: str, topic_index: int = 0) -> None:
    """Generate a script for the topic at index TOPIC_INDEX."""
    typer.echo(f"[stub] script for {game} (topic={topic_index})")


@app.command()
def metadata(game: str) -> None:
    """Generate metadata (titles, description, tags) for the latest approved script."""
    typer.echo(f"[stub] metadata for {game}")


@app.command("render-video")
def render_video(game: str) -> None:
    """(Windows) Drive Premiere to render the video from the approved script."""
    typer.echo(f"[stub] render-video for {game}")


@app.command("render-thumb")
def render_thumb(game: str) -> None:
    """(Windows) Drive Photoshop to render the thumbnail."""
    typer.echo(f"[stub] render-thumb for {game}")


@app.command()
def upload(game: str, privacy: str = "private") -> None:
    """Upload the rendered video to YouTube."""
    typer.echo(f"[stub] upload for {game} (privacy={privacy})")


@app.command("ingest-transcripts")
def ingest_transcripts(
    urls_file: str = "data/corpus/video_urls.txt",
    language: str = "en",
    model_size: str = "small",
    limit: int = 0,
) -> None:
    """Download + transcribe past videos from URLS_FILE to build the style corpus.

    Idempotent: video IDs already present under data/corpus/transcripts/ are skipped.
    Pass --limit N to transcribe only the first N URLs (useful for a smoke test).
    """
    from pathlib import Path
    from .paths import REPO_ROOT
    from .transcribe.whisper_runner import transcribe_urls

    path = Path(urls_file)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        typer.echo(f"error: urls file not found: {path}", err=True)
        raise typer.Exit(1)

    urls = [
        s.strip()
        for s in path.read_text(encoding="utf-8").splitlines()
        if s.strip() and not s.lstrip().startswith("#")
    ]
    if limit > 0:
        urls = urls[:limit]
    typer.echo(f"ingesting {len(urls)} URL(s) from {path}")
    results = transcribe_urls(urls, language=language, model_size=model_size)
    new = sum(1 for r in results if not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    typer.echo(f"done: {new} new, {skipped} skipped")


if __name__ == "__main__":
    app()
