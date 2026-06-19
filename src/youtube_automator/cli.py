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

import sys

import typer

# Subprocess pipes on Windows default to cp1252, which can't encode the
# LLM-generated emojis/curly quotes that show up in titles, descriptions
# and topic blurbs. Reconfigure stdout/stderr to UTF-8 so rich.print and
# plain print() both stay safe regardless of how yta is invoked.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:        # noqa: BLE001 — best-effort
            pass

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
def topics(
    game: str,
    n: int = 5,
    no_style: bool = False,
    idea: str = typer.Option(
        "",
        "--idea",
        help="Optional free-text direction. When set, topics are steered toward "
        "this idea/angle; when empty, topics rank purely on SEO / appeal / recency.",
    ),
) -> None:
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
    from .script.guardrails import check_topics
    from .script.style_corpus import style_prompt

    g = get_game(game)
    items = latest_snapshot(g)
    if not items:
        typer.echo("no snapshot found — run `yta research` first", err=True)
        raise typer.Exit(1)

    excerpt = "" if no_style else style_prompt()
    if idea.strip():
        typer.echo(f"steering topics toward: {idea.strip()!r}")
    candidates = propose(g, items, n=n, style_excerpt=excerpt, steer=idea)
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

    # Non-blocking §4.5 backstop: flag any topic that states a concrete stat
    # with no source so the user can verify or drop it before scripting.
    topic_warnings = check_topics(candidates)
    if topic_warnings:
        console.print("\n[bold yellow]Topic grounding warnings (verify before scripting):[/]")
        for v in topic_warnings:
            console.print(f"  - [yellow]{v.rule}[/]: {v.detail}")

    console.print(f"\n[dim]Saved {len(candidates)} candidates to {out_dir / 'topics_latest.json'}[/]")


@app.command()
def script(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video slug; output -> <video_slug>/script.json."),
    topic: int = 0,
    no_style: bool = False,
) -> None:
    """Generate a script for the topic at index TOPIC from `yta topics`."""
    import json
    from rich.console import Console

    from .config import get_game
    from .ideation.topic_generator import TopicCandidate
    from .paths import OUTPUTS_DIR, ensure_dirs, recordings_root
    from .research.aggregator import latest_snapshot
    from .script.generator import generate as gen_script
    from .script.guardrails import check_script
    from .script.style_corpus import style_prompt

    g = get_game(game)
    game_dir = OUTPUTS_DIR / g.slug
    out_dir = game_dir / video_slug          # per-video layout (batch-safe)
    # Pre-create the recordings drop folder so the user can drag the raw
    # gameplay fragments straight in after script+metadata land.
    recordings_dir = recordings_root() / g.slug / video_slug
    recordings_dir.mkdir(parents=True, exist_ok=True)
    topics_path = game_dir / "topics_latest.json"
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
    (out_dir / "script.json").write_text(
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
    console.print(f"\n[dim]Saved script to {out_dir / 'script.json'}[/]")
    console.print(f"[dim]Recordings folder ready: {recordings_dir}[/]")


@app.command()
def metadata(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video slug; reads <video_slug>/script.json."),
    n: int = 3,
    no_style: bool = False,
    idea: str = typer.Option(
        "",
        "--idea",
        help="Optional creator angle. When set, titles/thumbnail-copy/tags are "
        "steered toward this theme (no clickbait the video doesn't deliver).",
    ),
) -> None:
    """Generate metadata (titles, description, tags) for a per-video script."""
    import json
    from rich.console import Console

    from .config import get_game
    from .metadata.generator import generate as gen_metadata
    from .paths import OUTPUTS_DIR, ensure_dirs
    from .script.generator import Script
    from .script.style_corpus import style_prompt

    g = get_game(game)
    out_dir = OUTPUTS_DIR / g.slug / video_slug
    script_path = out_dir / "script.json"
    if not script_path.exists():
        typer.echo(
            f"no script — run `yta script {game} {video_slug} --topic N` first",
            err=True,
        )
        raise typer.Exit(1)
    s = Script.model_validate_json(script_path.read_text(encoding="utf-8"))

    excerpt = "" if no_style else style_prompt()
    if idea.strip():
        typer.echo(f"steering metadata toward: {idea.strip()!r}")
    m = gen_metadata(g, s, n_titles=n, style_excerpt=excerpt, steer=idea)

    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(
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
    console.print(f"\n[dim]Saved metadata to {out_dir / 'metadata.json'}[/]")


@app.command()
def cut(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video folder name (the edit's slug)."),
    fragments_dir: str = typer.Option(
        "", help="Folder with recorded fragments. Default: <recordings_root>/<game>/<video_slug>."
    ),
    snap: str = typer.Option(
        "", help="Override promo snap: fragment_boundary | keep_boundary | exact."
    ),
) -> None:
    """Silence-trim recorded fragments and compute the edit plan.

    Reads the ordered fragments, runs ffmpeg silencedetect, computes the
    timeline (gameplay duration, promo insertion point, total), and writes
    data/outputs/<slug>/<video_slug>/edit_plan.json for inspection before
    `yta render-video`.
    """
    from pathlib import Path

    from rich.console import Console

    from .adobe.edit_plan import build_edit_plan
    from .config import get_game
    from .paths import OUTPUTS_DIR, REPO_ROOT, recordings_root

    g = get_game(game)
    if fragments_dir:
        frags = Path(fragments_dir)
        if not frags.is_absolute():
            frags = (REPO_ROOT / frags).resolve()
    else:
        frags = recordings_root() / g.slug / video_slug
    if not frags.exists():
        typer.echo(f"fragments folder not found: {frags}", err=True)
        raise typer.Exit(1)

    console = Console()
    console.print(f"[bold]Cutting[/] {g.display_name} / {video_slug}  ([dim]{frags}[/])")
    try:
        plan = build_edit_plan(g, video_slug, frags, snap_boundaries=snap or None)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)

    out = OUTPUTS_DIR / g.slug / video_slug / "edit_plan.json"
    plan.write(out)

    for fr in plan.fragments:
        trimmed = fr.probe_duration_sec - fr.kept_duration_sec
        console.print(
            f"  [cyan]#{fr.index}[/] {Path(fr.path).name}  "
            f"{fr.probe_duration_sec:.1f}s -> {fr.kept_duration_sec:.1f}s "
            f"([yellow]-{trimmed:.1f}s[/], {len(fr.keep_segments)} segs)"
        )
    mins = int(plan.total_duration_sec // 60)
    secs = int(plan.total_duration_sec % 60)
    console.print(
        f"\n[bold]Gameplay:[/] {plan.gameplay_duration_sec:.1f}s"
    )
    if plan.promo.present:
        console.print(
            f"[bold]Promo:[/] {plan.promo.block_duration_sec:.1f}s block, "
            f"inserted @ {plan.promo_insertion_sec:.1f}s "
            f"([dim]{len(plan.promo.subclips)} frozen subclips[/])"
        )
    else:
        console.print("[bold]Promo:[/] none for this game")
    console.print(f"[bold]Total:[/] {plan.total_duration_sec:.1f}s ({mins}:{secs:02d})")
    console.print(f"\n[dim]Saved edit plan to {out}[/]")


@app.command()
def batch(
    game: str,
    recordings_dir: str = typer.Option(
        "", help="Root holding one folder per video. Default: <recordings_root>/<game>."
    ),
    skip_existing: bool = typer.Option(
        True, help="Skip videos whose .prproj already exists."
    ),
    auto_render: bool = typer.Option(
        False, "--auto-render",
        help="Also queue every built .prproj for AME via the CEP panel.",
    ),
    force_render: bool = typer.Option(
        False, "--force-render",
        help="Re-render videos whose MP4 already exists (default: preserve).",
    ),
) -> None:
    """Cut + rebuild EVERY per-video folder (the record-many-in-an-afternoon flow).

    Layout: <recordings_root>/<game>/<video_slug>/NNN_*.mp4
    For each folder: silence-trim -> edit_plan.json -> self-contained .prproj.
    Pure offline (no Premiere); leaves N projects ready to export. Pass
    --auto-render to also export all MP4s in a single Premiere session.
    """
    from pathlib import Path

    from rich.console import Console

    from .adobe.auto_render import render_preset, run_jobs
    from .adobe.edit_plan import build_edit_plan
    from .adobe.prproj_rebuild import rebuild
    from .config import get_game
    from .paths import OUTPUTS_DIR, premiere_templates_dir, recordings_root

    g = get_game(game)
    root = Path(recordings_dir) if recordings_dir else recordings_root() / g.slug
    if not root.exists():
        typer.echo(f"recordings root not found: {root}", err=True)
        raise typer.Exit(1)

    folders = sorted(d for d in root.iterdir() if d.is_dir())
    if not folders:
        typer.echo(f"no per-video folders under {root}", err=True)
        raise typer.Exit(1)

    pt = g.premiere_template
    template = premiere_templates_dir() / (pt.template_filename or f"{g.slug}.prproj")
    if not template.exists():
        typer.echo(f"template not found: {template}", err=True)
        raise typer.Exit(1)

    console = Console()
    console.print(f"[bold]Batch[/] {g.display_name}: {len(folders)} video folder(s)")
    ok, skipped, failed = 0, 0, 0
    for folder in folders:
        slug = folder.name
        out = OUTPUTS_DIR / g.slug / slug / f"{slug}.prproj"
        if skip_existing and out.exists():
            console.print(f"  [dim]skip[/] {slug} (already built)")
            skipped += 1
            continue
        try:
            plan = build_edit_plan(g, slug, folder)
            plan.write(OUTPUTS_DIR / g.slug / slug / "edit_plan.json")
            path, _log = rebuild(plan, template, out)
            mm = int(plan.total_duration_sec // 60)
            ss = int(plan.total_duration_sec % 60)
            console.print(
                f"  [green]ok[/] {slug}  {len(plan.fragments)} frag -> "
                f"{mm}:{ss:02d}  {path.name}"
            )
            ok += 1
        except Exception as e:  # noqa: BLE001 — report & continue the batch
            console.print(f"  [red]FAIL[/] {slug}: {type(e).__name__}: {e}")
            failed += 1
    console.print(
        f"\n[bold]Built:[/] {ok} new, {skipped} skipped, {failed} failed."
    )

    if auto_render:
        jobs = []
        for folder in folders:
            slug = folder.name
            prproj = OUTPUTS_DIR / g.slug / slug / f"{slug}.prproj"
            mp4 = OUTPUTS_DIR / g.slug / slug / f"{slug}.mp4"
            if not prproj.exists() or (skip_existing and mp4.exists()):
                continue
            jobs.append({
                "project": str(prproj), "sequence": pt.sequence_name,
                "output": str(mp4), "preset": str(render_preset()),
            })
        if not jobs:
            console.print("[dim]auto-render: nothing to do[/]")
            return
        console.print(
            f"[bold]Auto-render:[/] {len(jobs)} job(s) in one Premiere session "
            "(it will open, render every project, and quit)"
        )
        summary = run_jobs(jobs, timeout_s=3600 * 6, force=force_render)  # up to 6h
        console.print(f"  rendered {len(summary['done'])}/{summary['queued']} "
                      f"in {summary['elapsed_s']}s")
        if summary["missing"]:
            console.print(f"[red]missing:[/] {summary['missing']}")


@app.command("render-video")
def render_video(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video slug (same as `yta cut`)."),
    auto_render: bool = typer.Option(
        False, "--auto-render",
        help="Queue for AME via the CEP panel (or F5 yta_encoder.jsx as fallback).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-render even if <slug>.mp4 already exists (otherwise it's preserved).",
    ),
) -> None:
    """Rebuild the .prproj offline from the edit plan (no Premiere scripting).

    Reads data/outputs/<slug>/<video_slug>/edit_plan.json and the game's
    nest-migrated template, retimes the existing clips (effects preserved),
    and writes <video_slug>.prproj. Open it in Premiere and export, OR pass
    --auto-render to queue it for AME (requires AME 2020 + yta_render.epr).
    """
    from rich.console import Console

    from .adobe.auto_render import render_preset, run_jobs
    from .adobe.edit_plan import EditPlan
    from .adobe.prproj_rebuild import rebuild
    from .config import get_game
    from .paths import MIN_RENDER_FREE_GB, OUTPUTS_DIR, free_space_gb, premiere_templates_dir

    g = get_game(game)
    # Disk-space pre-flight: AME can die mid-export with a cryptic
    # "Error compiling movie" when the drive fills up. WARN but do NOT block
    # — the user decides (the GUI already confirms before queueing). When we
    # proceed under the threshold, auto-clear Adobe's media cache to reclaim
    # space (safe — Adobe regenerates it; skipped if Premiere/AME are open).
    if auto_render:
        free = free_space_gb(OUTPUTS_DIR / g.slug)
        if free < MIN_RENDER_FREE_GB:
            typer.echo(
                f"WARNING: low disk space — {free:.1f} GB free, recommended "
                f">= {MIN_RENDER_FREE_GB:.0f} GB. Rendering anyway.",
                err=True,
            )
            from .adobe.auto_render import clear_media_cache
            res = clear_media_cache()
            if res["ran"]:
                freed = res["freed_bytes"] / (1024 ** 3)
                now = free_space_gb(OUTPUTS_DIR / g.slug)
                typer.echo(
                    f"Auto-cleared Adobe media cache: freed {freed:.1f} GB "
                    f"-> {now:.1f} GB free.",
                    err=True,
                )
            else:
                typer.echo(
                    "Could not auto-clear the media cache (Premiere/AME is "
                    "running). Close them first if AME later runs out of space.",
                    err=True,
                )
    plan_path = OUTPUTS_DIR / g.slug / video_slug / "edit_plan.json"
    if not plan_path.exists():
        typer.echo(f"no edit plan — run `yta cut {game} {video_slug}` first", err=True)
        raise typer.Exit(1)
    plan = EditPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    pt = g.premiere_template
    template = premiere_templates_dir() / (pt.template_filename or f"{g.slug}.prproj")
    if not template.exists():
        typer.echo(f"template not found: {template}", err=True)
        raise typer.Exit(1)

    out = OUTPUTS_DIR / g.slug / video_slug / f"{video_slug}.prproj"
    console = Console()
    console.print(f"[bold]Rebuilding[/] {g.display_name} / {video_slug}")
    path, log = rebuild(plan, template, out)
    for line in log:
        console.print(f"  [dim]{line}[/]")
    console.print(f"\n[green]Wrote[/] {path}")

    if auto_render:
        mp4 = OUTPUTS_DIR / g.slug / video_slug / f"{video_slug}.mp4"
        console.print(f"[bold]Auto-render:[/] launching Premiere -> {mp4.name}")
        summary = run_jobs([{
            "project": str(path),
            "sequence": pt.sequence_name,
            "output": str(mp4),
            "preset": str(render_preset()),
        }], force=force)
        if summary["missing"]:
            console.print(f"[red]missing:[/] {summary['missing']}")
            raise typer.Exit(1)
        if summary["queued"] == 0:
            console.print("[yellow]nothing rendered[/] (mp4 already present; use --force).")
        else:
            console.print(f"[green]rendered[/] {mp4} in {summary['elapsed_s']}s")
            # Post-render audio mux. The .prproj does NOT contain the gameplay
            # voice (Premiere's audio-clone path de-dupes to the blueprint);
            # splice it in here from the WAV emitted by rebuild.
            from .adobe.audio_mux import mux_gameplay_audio
            gp_wav = OUTPUTS_DIR / g.slug / video_slug / f"{video_slug}_gameplay_audio.wav"
            if gp_wav.exists():
                console.print(f"[bold]Muxing gameplay voice[/] <- {gp_wav.name}")
                try:
                    mux_gameplay_audio(mp4, gp_wav, plan)
                    console.print("[green]voice muxed into mp4[/]")
                except Exception as e:  # noqa: BLE001
                    console.print(f"[red]voice mux failed[/]: {type(e).__name__}: {e}")
            else:
                console.print(f"[yellow]no gameplay WAV[/] at {gp_wav} — mp4 ships music-only")
    else:
        console.print("[dim]Open it in Premiere and export, or rerun with --auto-render.[/]")




@app.command("render-thumb")
def render_thumb(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video slug; reads <video_slug>/metadata.json."),
    top: str = typer.Option("", help="Override top text (else split from metadata.thumbnail_copy)."),
    bottom: str = typer.Option("", help="Override bottom text."),
    template_index: int = typer.Option(-1, help="Force template index (else rotation)."),
) -> None:
    """Render <video_slug>.png via Photoshop (COM, no F5 / no CEP).

    Picks the next template from assets/photoshop_templates/<game>/ (rotation
    = number of existing PNGs across the game's videos, modulo template count).
    Reads thumbnail_copy from metadata.json and splits into top/bottom text.
    """
    from rich.console import Console

    from .adobe.photoshop import (
        discover_templates,
        last_thumbnail_fit,
        next_template_index,
        render_thumbnail,
    )
    from .config import get_game

    g = get_game(game)
    templates = discover_templates(g)
    if not templates:
        typer.echo("no .psd templates found for this game", err=True)
        raise typer.Exit(1)
    # Resolve the index HERE (once) so the preview line is accurate and the
    # rotation state advances exactly once. render_thumbnail then receives a
    # concrete index and won't advance again.
    idx = (
        template_index
        if template_index >= 0
        else next_template_index(g, video_slug, len(templates))
    )
    console = Console()
    console.print(
        f"[bold]Rendering thumb[/] {g.display_name} / {video_slug}  "
        f"(template #{idx}: {templates[idx].name})"
    )
    try:
        out = render_thumbnail(
            g, video_slug,
            top=top or None, bottom=bottom or None,
            template_index=idx,
        )
    except Exception as e:  # noqa: BLE001 — show what Photoshop reported
        typer.echo(f"error: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1)
    console.print(f"[green]wrote[/] {out}")

    # Text-fit report: flag any thumbnail text that was shrunk to fit its
    # Smart Object, or that STILL overflows (clipped) after auto-fit + the
    # shorten retry — so the user can eyeball or hand-tweak that one.
    for f in last_thumbnail_fit():
        role, scale = f.get("role", "?"), f.get("final_scale", 1.0)
        if f.get("overflow"):
            console.print(
                f"  [bold red]⚠ {role} text still overflows[/] (scaled to "
                f"{scale:.0%}); shorten the copy or widen that Smart Object."
            )
        elif scale < 0.99:
            console.print(f"  [yellow]{role} text shrunk[/] to {scale:.0%} to fit its box")
        elif scale > 1.01:
            console.print(f"  [green]{role} text enlarged[/] to {scale:.0%} to fill its box")


@app.command()
def upload(
    game: str,
    video_slug: str = typer.Argument(..., help="Per-video slug; reads <video_slug>/metadata.json."),
    video: str = typer.Option("", help="Path to the rendered MP4. Default: <video_slug>/<video_slug>.mp4."),
    thumbnail: str = typer.Option("", help="Path to the thumbnail PNG. Default: <video_slug>/<video_slug>.png if present."),
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
    """Upload a per-video rendered MP4 to YouTube.

    Reads:
      data/outputs/<slug>/<video_slug>/metadata.json
    Default video/thumbnail paths use the same per-video folder.

    First run prompts for OAuth consent (browser opens). Cached afterwards.
    """
    from datetime import datetime
    from pathlib import Path

    from .config import get_game
    from .metadata.generator import VideoMetadata
    from .paths import OUTPUTS_DIR
    from .upload.youtube import upload as do_upload

    g = get_game(game)
    out_dir = OUTPUTS_DIR / g.slug / video_slug
    metadata_path = out_dir / "metadata.json"
    if not metadata_path.exists():
        typer.echo(
            f"no metadata — run `yta metadata {game} {video_slug}` first", err=True
        )
        raise typer.Exit(1)
    metadata = VideoMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    if not video:
        video = str(out_dir / f"{video_slug}.mp4")
    if not thumbnail:
        candidate = out_dir / f"{video_slug}.png"
        if candidate.exists():
            thumbnail = str(candidate)
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


@app.command("watch-and-upload")
def watch_and_upload(
    game: str = typer.Argument(..., help="A game slug (e.g. 'lom', 'loe', 'dsv') or 'all' for every game."),
    once: bool = typer.Option(
        True, "--once/--daemon",
        help="--once: scan, upload everything ready, exit. --daemon: keep polling.",
    ),
    poll_s: int = typer.Option(60, help="--daemon poll interval in seconds."),
    title_index: int = typer.Option(0, help="Which title candidate to publish."),
    yes: bool = typer.Option(True, "--yes/--prompt", help="Skip per-video confirmation."),
) -> None:
    """Auto-upload every ready MP4 with one-video-per-day scheduling.

    For each <video_slug>.mp4 in data/outputs/<game>/*/ that has metadata.json
    and no uploaded marker, allocate the next free 18:30 slot across BOTH
    games and upload as `private` with publishAt set. YouTube auto-publishes
    at the scheduled time. Quota errors defer the rest to the next run.
    """
    import time as _time
    from pathlib import Path

    from rich.console import Console

    from .config import get_game, get_games, get_settings
    from .metadata.generator import VideoMetadata
    from .paths import OUTPUTS_DIR
    from .upload.schedule import ScheduleStore, ScheduledItem, next_slot
    from .upload.youtube import upload as do_upload

    console = Console()
    schedule_cfg = get_settings().schedule
    games = (
        list(get_games().values())
        if game.lower() == "all"
        else [get_game(game)]
    )

    def scan() -> int:
        store = ScheduleStore.load()
        uploaded_now = 0
        for g in games:
            base = OUTPUTS_DIR / g.slug
            if not base.exists():
                continue
            for vdir in sorted(base.iterdir()):
                if not vdir.is_dir():
                    continue
                slug = vdir.name
                mp4 = vdir / f"{slug}.mp4"
                meta = vdir / "metadata.json"
                mark = vdir / "uploaded.json"
                if mark.exists() or not mp4.exists() or not meta.exists():
                    continue
                m = VideoMetadata.model_validate_json(meta.read_text(encoding="utf-8"))
                if m.description_violations:
                    console.print(
                        f"  [yellow]skip[/] {g.slug}/{slug}: description violations"
                    )
                    continue
                if title_index >= len(m.candidates):
                    console.print(f"  [yellow]skip[/] {g.slug}/{slug}: no title #{title_index}")
                    continue
                chosen_title = m.candidates[title_index].title
                publish_at = next_slot(
                    schedule_cfg, busy=[i.publish_at for i in store.items]
                )
                console.print(
                    f"  [bold]{g.slug}/{slug}[/]: {chosen_title!r}\n"
                    f"    publish_at={publish_at.isoformat()}"
                )
                if not yes and not typer.confirm("    upload?", default=True):
                    continue
                try:
                    result = do_upload(
                        video_path=mp4,
                        thumbnail_path=(vdir / f"{slug}.png") if (vdir / f"{slug}.png").exists() else None,
                        metadata=m,
                        chosen_title=chosen_title,
                        game=g,
                        publish_at=publish_at,
                        privacy_status="private",
                    )
                except Exception as e:  # noqa: BLE001 — defer on any API error
                    console.print(f"    [red]upload failed[/]: {type(e).__name__}: {e}")
                    continue
                item = ScheduledItem(
                    game=g.slug, video_slug=slug,
                    publish_at=publish_at, video_id=result.video_id, url=result.url,
                )
                store.add(item)
                store.save()
                mark.write_text(item.model_dump_json(indent=2), encoding="utf-8")
                console.print(f"    [green]uploaded[/] {result.url}")
                # Enqueue Discord + Twitter companion posts at publishAt.
                from .social.queue import SocialQueue, build_companion_posts
                sq = SocialQueue.load()
                for p in build_companion_posts(
                    game_slug=g.slug, video_slug=slug,
                    video_url=result.url, title=chosen_title,
                    tags=m.tags, post_at=publish_at,
                ):
                    sq.add(p)
                sq.save()
                console.print(f"    [dim]social: queued {len(m.tags)} tag(s) at {publish_at.isoformat()}[/]")
                uploaded_now += 1
        return uploaded_now

    if once:
        n = scan()
        console.print(f"\n[bold]Done:[/] {n} upload(s).")
        return
    console.print(f"[bold]Daemon[/] every {poll_s}s. Ctrl+C to stop.")
    while True:
        n = scan()
        if n:
            console.print(f"[dim]uploaded {n}; sleeping {poll_s}s…[/]")
        _time.sleep(poll_s)


@app.command("fix-youtube")
def fix_youtube(
    game: str = typer.Argument(..., help="A game slug (e.g. 'lom', 'loe', 'dsv')."),
    video_slug: str = typer.Argument(..., help="The slug whose uploaded.json holds the video_id."),
) -> None:
    """Retry thumbnail + playlist for an already-uploaded video.

    Reads `data/outputs/<game>/<video_slug>/uploaded.json` for the
    video_id, then re-runs ONLY the post-upload side-effects (thumbnail
    upload + playlist insertion). Useful when the original upload
    succeeded but a hiccup (e.g. >2 MiB thumbnail) left the video on
    YouTube without its art.
    """
    import json
    from pathlib import Path

    from rich.console import Console

    from .config import get_game
    from .paths import OUTPUTS_DIR
    from .upload.youtube import _service, _set_thumbnail, _add_to_playlist

    console = Console()
    g = get_game(game)
    vdir = OUTPUTS_DIR / g.slug / video_slug
    mark = vdir / "uploaded.json"
    if not mark.exists():
        typer.echo(f"no uploaded.json at {mark}", err=True)
        raise typer.Exit(1)
    info = json.loads(mark.read_text(encoding="utf-8"))
    video_id = info.get("video_id")
    if not video_id:
        typer.echo("uploaded.json has no video_id", err=True)
        raise typer.Exit(1)

    yt = _service()
    thumb = vdir / f"{video_slug}.png"
    if thumb.exists():
        console.print(f"[bold]Thumbnail[/] -> {video_id}  ({thumb.stat().st_size/1024:.0f} KiB)")
        _set_thumbnail(yt, video_id, thumb)
    else:
        console.print(f"[yellow]no thumbnail at {thumb}[/]")
    if g.youtube.playlist_id:
        console.print(f"[bold]Playlist[/] {g.youtube.playlist_id} += {video_id}")
        _add_to_playlist(yt, video_id, g.youtube.playlist_id)
    else:
        console.print(f"[yellow]no playlist_id configured for {g.slug}[/]")
    console.print(f"\n[green]done[/] — https://youtu.be/{video_id}")


@app.command("update-youtube")
def update_youtube(
    game: str = typer.Argument(..., help="A game slug (e.g. 'lom', 'loe', 'dsv')."),
    video_slug: str = typer.Argument(..., help="The slug whose uploaded.json holds the video_id."),
    title_index: int = typer.Option(0, help="Which metadata title candidate to publish."),
    skip_thumbnail: bool = typer.Option(False, "--skip-thumbnail", help="Don't touch the thumbnail."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Push the CURRENT metadata.json (title/description/tags) + thumbnail onto
    an ALREADY-UPLOADED video.

    Reads `data/outputs/<game>/<video_slug>/uploaded.json` for the video_id and
    `metadata.json` for the new snippet, then calls videos.update + thumbnail
    set. Use after regenerating metadata/thumbnail for a video already live.
    """
    import json
    from rich.console import Console

    from .config import get_game
    from .metadata.generator import VideoMetadata
    from .paths import OUTPUTS_DIR
    from .upload.youtube import _service, _set_thumbnail, update_video_metadata

    console = Console()
    g = get_game(game)
    vdir = OUTPUTS_DIR / g.slug / video_slug
    mark = vdir / "uploaded.json"
    meta_path = vdir / "metadata.json"
    if not mark.exists():
        typer.echo(f"no uploaded.json at {mark} — is the video uploaded?", err=True)
        raise typer.Exit(1)
    if not meta_path.exists():
        typer.echo(f"no metadata.json at {meta_path}", err=True)
        raise typer.Exit(1)
    video_id = json.loads(mark.read_text(encoding="utf-8")).get("video_id")
    if not video_id:
        typer.echo("uploaded.json has no video_id", err=True)
        raise typer.Exit(1)

    m = VideoMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
    if m.description_violations:
        typer.echo("description guardrail violations — refusing to update:", err=True)
        for v in m.description_violations:
            typer.echo(f"  - {v}", err=True)
        raise typer.Exit(1)
    if title_index < 0 or title_index >= len(m.candidates):
        typer.echo(f"title_index {title_index} out of range (have {len(m.candidates)})", err=True)
        raise typer.Exit(1)
    title = m.candidates[title_index].title
    thumb = vdir / f"{video_slug}.png"

    console.print(f"[bold]Updating[/] https://youtu.be/{video_id}")
    console.print(f"  title: {title}")
    console.print(f"  tags:  {len(m.tags)}  |  description: {len(m.description)} chars")
    console.print(f"  thumbnail: {'(skip)' if skip_thumbnail else (str(thumb) if thumb.exists() else 'none')}")
    if not yes and not typer.confirm("Push this to the LIVE video?", default=False):
        typer.echo("aborted")
        raise typer.Exit(0)

    yt = _service()
    update_video_metadata(
        yt, video_id,
        title=title, description=m.description, tags=m.tags,
        category_id=g.youtube.default_category_id,
    )
    console.print("[green]snippet updated[/] (title + description + tags)")
    if not skip_thumbnail and thumb.exists():
        console.print(f"[bold]Thumbnail[/] -> {video_id} ({thumb.stat().st_size/1024:.0f} KiB)")
        _set_thumbnail(yt, video_id, thumb)
    console.print(f"\n[green]done[/] — https://youtu.be/{video_id}")


@app.command("social-daemon")
def social_daemon(
    once: bool = typer.Option(
        True, "--once/--daemon",
        help="--once: post every due item and exit. --daemon: keep polling.",
    ),
    poll_s: int = typer.Option(60, help="--daemon poll interval in seconds."),
) -> None:
    """Drain the social-posts queue: fire Discord/Twitter posts at their
    scheduled publishAt. Posts whose post_at is still in the future stay
    pending; posted items remain in the queue with status=posted."""
    import time as _time
    from datetime import datetime, timezone

    from rich.console import Console

    from .social.post import post_discord, post_twitter
    from .social.queue import SocialQueue

    console = Console()

    def drain() -> int:
        sq = SocialQueue.load()
        now = datetime.now(timezone.utc)
        due = sq.due(now)
        if not due:
            return 0
        n = 0
        for p in due:
            try:
                if p.channel == "discord":
                    url = post_discord(p.content)
                else:
                    url = post_twitter(p.content)
                if url:
                    p.status = "posted"
                    p.posted_at = datetime.now(timezone.utc)
                    console.print(f"  [green]posted[/] {p.channel} for {p.game}/{p.video_slug}")
                else:
                    console.print(f"  [yellow]skip[/] {p.channel}: no creds configured")
                n += 1
            except Exception as e:  # noqa: BLE001
                p.status = "failed"
                p.error = f"{type(e).__name__}: {e}"
                console.print(f"  [red]fail[/] {p.channel}: {p.error}")
        sq.save()
        return n

    if once:
        console.print(f"[bold]social-daemon[/] (once)")
        n = drain()
        console.print(f"\n[bold]Done:[/] processed {n} post(s).")
        return
    console.print(f"[bold]social-daemon[/] every {poll_s}s. Ctrl+C to stop.")
    while True:
        n = drain()
        if n:
            console.print(f"[dim]processed {n}; sleeping {poll_s}s…[/]")
        _time.sleep(poll_s)


@app.command("social-post")
def social_post_cli(
    channel: str = typer.Argument(..., help="'discord' or 'twitter'."),
    message: str = typer.Argument(..., help="The message body to post."),
) -> None:
    """Ad-hoc test poster — bypasses the queue (posts immediately)."""
    from .social.post import post_discord, post_twitter
    if channel == "discord":
        url = post_discord(message)
    elif channel == "twitter":
        url = post_twitter(message)
    else:
        typer.echo("channel must be 'discord' or 'twitter'", err=True)
        raise typer.Exit(1)
    if url:
        typer.echo(f"posted: {url}")
    else:
        typer.echo("skipped (no creds configured for this channel)")


@app.command("install-cep")
def install_cep() -> None:
    """One-time: install the YTA CEP panel into Premiere (kills the F5 step).

    Copies scripts/cep/YTA/ to %APPDATA%/Adobe/CEP/extensions/YTA/ and sets
    PlayerDebugMode=1 in HKCU\\Software\\Adobe\\CSXS.* so the unsigned
    extension is allowed to load. No admin required.

    After this, every Premiere launch auto-loads the panel; the panel reads
    data/tmp/yta_render_jobs.json and (only) when there's a queue, opens
    each project and queues to Adobe Media Encoder. Empty queue = silent
    no-op (Premiere works normally).
    """
    from .adobe.auto_render import install_cep_panel
    try:
        info = install_cep_panel()
    except (FileNotFoundError, OSError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"installed: {info['installed_at']}")
    typer.echo(f"PlayerDebugMode=1 set for CSXS versions: {info['csxs_versions_enabled']}")
    typer.echo(
        "\nRestart Premiere fully. The 'YTA Worker' panel auto-opens (Window > Extensions if hidden)."
    )


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
