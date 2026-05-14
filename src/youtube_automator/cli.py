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
    """Propose N topic candidates from the latest research snapshot.

    Persists the result to data/outputs/<slug>/topics_latest.json so that
    `yta script <game> --topic N` can pick from this list.
    """
    import json
    from rich.console import Console
    from rich.table import Table

    from .config import get_game
    from .ideation.topic_generator import propose
    from .paths import OUTPUTS_DIR, ensure_dirs
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

    # Persist so `yta script --topic N` can read it.
    ensure_dirs()
    out_dir = OUTPUTS_DIR / g.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "topics_latest.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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
    console.print(f"\n[dim]Saved {len(candidates)} candidates to {out_dir / 'topics_latest.json'}[/]")


@app.command()
def script(game: str, topic: int = 0, no_style: bool = False) -> None:
    """Generate a script for the topic at index TOPIC from `yta topics`."""
    import json
    from rich.console import Console

    from .config import get_game
    from .ideation.topic_generator import TopicCandidate
    from .paths import OUTPUTS_DIR, ensure_dirs
    from .research.aggregator import latest_snapshot
    from .script.generator import generate as gen_script
    from .script.guardrails import check_script
    from .script.style_corpus import style_prompt

    g = get_game(game)
    out_dir = OUTPUTS_DIR / g.slug
    topics_path = out_dir / "topics_latest.json"
    if not topics_path.exists():
        typer.echo("no topics file — run `yta topics` first", err=True)
        raise typer.Exit(1)
    raw_topics = json.loads(topics_path.read_text(encoding="utf-8"))
    if topic < 0 or topic >= len(raw_topics):
        typer.echo(f"topic index {topic} out of range (have {len(raw_topics)})", err=True)
        raise typer.Exit(1)
    chosen = TopicCandidate.model_validate(raw_topics[topic])

    items = latest_snapshot(g)
    if not items:
        typer.echo("no snapshot found — run `yta research` first", err=True)
        raise typer.Exit(1)

    excerpt = "" if no_style else style_prompt()
    typer.echo(f"generating script for topic #{topic}: {chosen.title_hook}")
    s = gen_script(g, chosen, items, style_excerpt=excerpt)

    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script_latest.json").write_text(
        s.model_dump_json(indent=2), encoding="utf-8"
    )

    violations = check_script(s, g)
    console = Console()
    console.print(f"\n[bold]Script[/]: {len(s.segments)} segments, ~{s.total_duration_s_estimate}s "
                  f"({s.total_duration_s_estimate // 60}:{s.total_duration_s_estimate % 60:02d})")
    for i, seg in enumerate(s.segments):
        console.print(
            f"\n[cyan]#{i} [{seg.kind}][/] (~{seg.duration_s_estimate}s)"
        )
        if seg.text:
            console.print(f"  {seg.text}")
        if seg.shot_notes:
            console.print(f"  [dim]shot: {seg.shot_notes}[/]")
        if seg.citations:
            console.print(f"  [dim]cite: {', '.join(seg.citations)}[/]")

    if violations:
        console.print("\n[bold red]Guardrail violations:[/]")
        for v in violations:
            console.print(f"  - [yellow]{v.rule}[/]: {v.detail}")
    else:
        console.print("\n[bold green]All guardrails passed.[/]")
    console.print(f"\n[dim]Saved script to {out_dir / 'script_latest.json'}[/]")


@app.command()
def metadata(game: str, n: int = 3, no_style: bool = False) -> None:
    """Generate metadata (titles, description, tags) for the latest script."""
    import json
    from rich.console import Console

    from .config import get_game
    from .metadata.generator import generate as gen_metadata
    from .paths import OUTPUTS_DIR, ensure_dirs
    from .script.generator import Script
    from .script.style_corpus import style_prompt

    g = get_game(game)
    out_dir = OUTPUTS_DIR / g.slug
    script_path = out_dir / "script_latest.json"
    if not script_path.exists():
        typer.echo("no script file — run `yta script` first", err=True)
        raise typer.Exit(1)
    s = Script.model_validate_json(script_path.read_text(encoding="utf-8"))

    excerpt = "" if no_style else style_prompt()
    m = gen_metadata(g, s, n_titles=n, style_excerpt=excerpt)

    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata_latest.json").write_text(
        m.model_dump_json(indent=2), encoding="utf-8"
    )

    console = Console()
    console.print(f"\n[bold]Title candidates ({len(m.candidates)}):[/]")
    for i, c in enumerate(m.candidates):
        console.print(f"\n[cyan]#{i}[/] [bold]{c.title}[/]")
        console.print(f"  Thumb: [yellow]{c.thumbnail_copy}[/]")
        console.print(f"  Why:   {c.expected_ctr_rationale}")
    console.print("\n[bold]Tags:[/]", ", ".join(m.tags))
    console.print("\n[bold]Description (preview):[/]")
    preview = "\n".join(m.description.splitlines()[:15])
    console.print(preview)
    console.print(f"  [dim]... ({len(m.description)} chars total)[/]")
    if m.description_violations:
        console.print("\n[bold red]Description guardrail violations:[/]")
        for v in m.description_violations:
            console.print(f"  - {v}")
    console.print(f"\n[dim]Saved metadata to {out_dir / 'metadata_latest.json'}[/]")


@app.command("render-video")
def render_video(game: str) -> None:
    """(Windows) Drive Premiere to render the video from the approved script."""
    typer.echo(f"[stub] render-video for {game}")


@app.command("render-thumb")
def render_thumb(game: str) -> None:
    """(Windows) Drive Photoshop to render the thumbnail."""
    typer.echo(f"[stub] render-thumb for {game}")


@app.command()
def upload(
    game: str,
    video: str = typer.Option(..., help="Path to the rendered MP4 to upload."),
    thumbnail: str = typer.Option("", help="Path to the thumbnail PNG (optional)."),
    title_index: int = typer.Option(0, help="Which title candidate to use (0-based)."),
    privacy: str = typer.Option(
        "private",
        help="'private' (default), 'unlisted', or 'public'. Use scheduled publish via --publish-at.",
    ),
    publish_at: str = typer.Option(
        "",
        help="Schedule publish: ISO 8601 ('2026-05-15T19:00:00Z') or 'YYYY-MM-DD HH:MM' (UTC).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Upload the rendered video to YouTube using the latest script + metadata.

    Reads:
      data/outputs/<slug>/script_latest.json
      data/outputs/<slug>/metadata_latest.json

    First run prompts for OAuth consent (browser opens). Cached afterwards.
    """
    import json
    from datetime import datetime
    from pathlib import Path

    from .config import get_game
    from .metadata.generator import VideoMetadata
    from .paths import OUTPUTS_DIR
    from .upload.youtube import upload as do_upload

    g = get_game(game)
    out_dir = OUTPUTS_DIR / g.slug
    metadata_path = out_dir / "metadata_latest.json"
    if not metadata_path.exists():
        typer.echo("no metadata file — run `yta metadata` first", err=True)
        raise typer.Exit(1)
    metadata = VideoMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    if title_index < 0 or title_index >= len(metadata.candidates):
        typer.echo(
            f"title_index {title_index} out of range (have {len(metadata.candidates)})", err=True
        )
        raise typer.Exit(1)
    chosen_title = metadata.candidates[title_index].title

    publish_dt: datetime | None = None
    if publish_at:
        try:
            publish_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except ValueError:
            try:
                publish_dt = datetime.strptime(publish_at, "%Y-%m-%d %H:%M")
            except ValueError:
                typer.echo(f"could not parse --publish-at {publish_at!r}", err=True)
                raise typer.Exit(1)

    if metadata.description_violations:
        typer.echo("description guardrail violations — refusing to upload:", err=True)
        for v in metadata.description_violations:
            typer.echo(f"  - {v}", err=True)
        raise typer.Exit(1)

    typer.echo(f"title: {chosen_title}")
    typer.echo(f"video: {video}")
    if thumbnail:
        typer.echo(f"thumbnail: {thumbnail}")
    typer.echo(f"privacy: {privacy}{' (scheduled)' if publish_dt else ''}")
    if publish_dt:
        typer.echo(f"publish at: {publish_dt.isoformat()}")

    if not yes and not typer.confirm("Proceed with upload?", default=False):
        typer.echo("aborted")
        raise typer.Exit(0)

    result = do_upload(
        video_path=Path(video),
        thumbnail_path=Path(thumbnail) if thumbnail else None,
        metadata=metadata,
        chosen_title=chosen_title,
        game=g,
        publish_at=publish_dt,
        privacy_status=privacy,
    )
    typer.echo(f"\nUploaded: {result.url}")


@app.command("paste-discord")
def paste_discord(
    game: str,
    channel: str = "creators-announce",
    from_clipboard: bool = False,
    edit: bool = False,
) -> None:
    """Append Discord channel content to the manual-paste inbox.

    Use this for upstream channels that can't be Followed (e.g. creators-announce,
    dev-feedback). The bot path handles followable channels automatically.

    Modes:
      --from-clipboard: read content from the system clipboard (pbpaste on macOS).
      --edit:           open the inbox file in $EDITOR (defaults to vim/nano).
      (default):        read from stdin until EOF.
    """
    import os
    import subprocess
    from datetime import datetime, timezone

    from .config import get_game
    from .research.sources.discord import inbox_path

    g = get_game(game)
    path = inbox_path(g)
    path.parent.mkdir(parents=True, exist_ok=True)

    if edit:
        editor = os.environ.get("EDITOR") or ("nano" if shutil_which("nano") else "vi")
        path.touch(exist_ok=True)
        os.execvp(editor, [editor, str(path)])
        return  # never reached

    if from_clipboard:
        try:
            content = subprocess.check_output(["pbpaste"], text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            typer.echo(f"clipboard read failed: {e}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo(f"paste content for #{channel}, then Ctrl+D:")
        import sys
        content = sys.stdin.read()

    if not content.strip():
        typer.echo("no content to append", err=True)
        raise typer.Exit(1)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    block = f"\n## {channel}\n[{stamp}] manual-paste: pasted-on-{stamp}\n{content.strip()}\n---\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    typer.echo(f"appended {len(content)} chars to {path}")


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


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
