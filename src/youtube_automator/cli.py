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
    typer.echo(f"[stub] research for {game}")


@app.command()
def topics(game: str, n: int = 5) -> None:
    """Propose N topic candidates from the latest research snapshot."""
    typer.echo(f"[stub] topics for {game} (n={n})")


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
def ingest_transcripts() -> None:
    """One-off: download + transcribe past videos to build the style corpus."""
    typer.echo("[stub] ingest-transcripts")


if __name__ == "__main__":
    app()
