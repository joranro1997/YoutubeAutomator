# YoutubeAutomator — context for Claude

This file is the cold-start brief. Read it before doing anything in this repo.
It captures the decisions and constraints that aren't obvious from the code.

## Who & what

- **User**: Jorge ("Midway"), Spanish speaker but the channel is 100% English.
- **Channel**: [@MidwayPaladin](https://www.youtube.com/@MidwayPaladin), channel
  ID `UC3-ezcoZJwBi3t1K4E0kQdg`.
- **Business**: paid YouTube creator with 2 videos/week per game covering
  sponsored mobile games from the same developer (all via the Aptoide
  affiliate network):
  - **Legend of Mushroom** — slug `lom`, affiliate code `MIDWAY`
  - **Legend of Elements** — slug `loe`, affiliate code `MIDLOE`
  - **Duck Survival** — slug `dsv`, affiliate code TODO (newest title;
    onboarded but awaiting contract values + first Premiere/Photoshop
    templates — see "Onboarding a new game" below)
- **Voice style**: high-energy English gamer voice. Real signatures:
  "INSANE", "MASSIVE", "BROKEN", "CRAZY", "STOP X", "DO THIS". Caps for
  emphasis. Rhetorical questions. Numbers in titles. The transcripts under
  `data/corpus/transcripts/` are the authoritative voice reference.

**Communicate with the user in Spanish (Spain).** All content the pipeline
produces (scripts, titles, descriptions, tags) is **English** because the
channel is English.

## Sponsor contract (NON-NEGOTIABLE)

Contract is with **Aptoide, S.A.** (an affiliate network, NOT the game
developer). Two analogous agreements — one per game — with identical clauses.
The key rules that the pipeline must honour:

- **§4.2** Content must be original; no third-party IP re-use.
- **§4.5** Statements reflect the influencer's **honest opinions and actual
  experiences**. Factual statements must be **verifiable**. → AI must not
  hallucinate game facts; every factual claim cites a research source.
- **§4.6** Comply with YouTube ToS (including 2024+ synthetic-content
  labelling rules — relevant if we ever clone voice).
- **§4.9** No disparagement of Aptoide.
- **§4.11** Never present Aptoide as a place where paid apps can be
  downloaded for free.
- **Appendix**: 1 mention of Aptoide per video + affiliate code in the
  description. The verbal Aptoide mention is satisfied by the **pre-recorded
  ad segment** the user splices into every video (its placeholder is the
  `aptoide_ad_marker` segment in generated scripts).
- **Pre-approval requirement**: contract says yes, in practice Aptoide does
  not gate uploads. The pipeline publishes directly.

These are enforced by `src/youtube_automator/script/guardrails.py` and the
`contract_guardrails` section in `config/settings.yaml`.

**One Aptoide-approved framing that LOOKS suspicious but is fine**: titles
or descriptions like "No downloads, no Aptoide app needed — play in browser"
are **explicitly approved** by Aptoide because they promote the browser-play
short link. Do not flag these as §4.9 / §4.11 violations.

## Pipeline & phases

End-to-end CLI (`yta`, the entrypoint defined in `pyproject.toml`):

```
yta research <game>                      # pull YouTube + Discord + Reddit signal
yta topics <game> --n 5                  # Claude ranks topic candidates
yta topics <game> --idea "..."           # steer topics toward an idea (empty = pure SEO)
yta script <game> --topic N              # Claude writes the script
yta metadata <game> --n 3                # title variants + description + SEO tags
yta upload <game> --video ... --thumbnail ... --title-index 0 --privacy private
yta paste-discord <game>                 # manual Discord paste for non-followable chans
yta ingest-transcripts                   # one-off: build style corpus from past videos
```

Phase status (see commits for chronology):

| Phase | Status | Notes |
|---|---|---|
| 1A — LLM wrapper + transcription | ✅ done | `anthropic` SDK + prompt caching; yt-dlp + faster-whisper |
| 1B — Research aggregator | ✅ done | YouTube via yt-dlp, Discord via real bot in user's own server, Reddit pending API access approval |
| 1C — Topic / Script / Metadata generators | ✅ done | All three with real Claude calls, recent-uploads dedup, SEO-tuned tags |
| 2 — YouTube upload | ✅ done | OAuth desktop flow, resumable upload, thumbnails(), scheduled publish |
| 3 — Adobe Premiere + Photoshop ExtendScript | 🟡 pending | Windows-only at runtime |

## Layout

```
config/
  settings.yaml          # channel-wide settings + contract guardrails + description templates
  games.yaml             # per-game sources, sponsorship links, YouTube defaults
src/youtube_automator/
  config.py paths.py     # config loader (Pydantic); cross-platform paths
  llm/claude.py          # Anthropic wrapper with SystemBlock caching
  research/
    sources/{reddit,discord,youtube,web}.py
    aggregator.py types.py
  ideation/
    topic_generator.py   # Claude call; reads research snapshot
    recent_uploads.py    # dedup: knows what the user just published
  script/
    generator.py         # Claude call; outputs structured segments
    guardrails.py        # contract checks (script + description)
    style_corpus.py      # samples user's transcripts as cacheable style block
  metadata/generator.py  # title variants + description + SEO tags
  transcribe/whisper_runner.py
  upload/youtube.py      # YouTube Data API v3 client
  adobe/                 # Phase 3 stubs (Premiere + Photoshop, Windows-only)
  cli.py                 # Typer commands; one per pipeline stage
scripts/ tests/
data/                    # corpus, research snapshots, outputs (gitignored)
secrets/                 # OAuth client_secret + token (gitignored)
```

Each pipeline stage persists its output to `data/outputs/<slug>/`:
`topics_latest.json` → `script_latest.json` → `metadata_latest.json`. The
next stage reads the previous one's `_latest.json`.

## Conventions / preferences

- **Code**: Python 3.11+, Pydantic v2, Typer. Cross-platform — dev on Mac,
  runtime on Windows. Only `src/youtube_automator/adobe/` is Windows-only.
- **Paths**: always go through `src/youtube_automator/paths.py`. OS-specific
  roots overridable via env vars (`ASSETS_ROOT`, `PREMIERE_TEMPLATES_DIR`,
  `PHOTOSHOP_TEMPLATES_DIR`).
- **Configs**: source of truth lives in `config/*.yaml`. Don't hardcode
  game-specific data in Python.
- **Secrets**: `.env` for tokens, `secrets/` for OAuth JSONs. Both
  gitignored. NEVER commit secrets, NEVER read API keys aloud to chat.
- **Commits**: descriptive multi-paragraph messages, `Co-authored-by: Atenea
  Agent <srv_atenea_gitlab@ofidona.net>` trailer. Past commit messages are
  the style reference.
- **Discord scraping**: ToS-safe ONLY. Real bot in the user's own server
  (`714631239507902496`) reading mirror channels created via Discord's
  Follow feature. Selfbot / user-token scraping is explicitly out of scope.
- **Voice cloning / synthetic gameplay**: contractually risky (§4.5, §11),
  not implemented. User records his own voice over the generated script.
- **Style corpus**: deterministic sampling (seed=7) so Anthropic prompt
  caching pays off across calls in a session.

## Open items (when resuming)

- **Phase 3 — Adobe automation (Windows)**: Premiere ExtendScript that
  populates the user's existing `.prproj` template with gameplay clips +
  the pre-recorded Aptoide ad + lower-third texts; Photoshop ExtendScript
  for the thumbnail template. Will need user to share copies of a real
  `.prproj` and `.psd` template + a screenshot of his Premiere workspace.
- **Reddit**: API access application is submitted. When approved, paste
  the credentials in `.env` and the existing
  `src/youtube_automator/research/sources/reddit.py` will start producing
  items on the next `yta research` run. No code change needed.
- **Discord mirror channels**: Follow setup is done for everything that
  was followable. The MESSAGE_CONTENT intent is ON. The
  creator-announce channels (LoM + LoE) are NOT followable — handle via
  `yta paste-discord <game>` when relevant content drops.
- **Tag SEO** can be made even better by classifying tag-buckets explicitly
  in the JSON output (currently flat list); only worth it if tag quality
  drifts.

## Onboarding a new game (e.g. Duck Survival → `dsv`)

The pipeline is data-driven; a new game is mostly config + two templates the
user builds by hand. Done for `dsv` already (values still TODO):

1. **`config/games.yaml`** — add a `<game_key>:` block. Required: `slug`,
   `sources`, `sponsorship.description_template_id`, `premiere_template`
   (track roles!), `photoshop_template`. Mark contract-derived values
   (`affiliate_code`, `download_link`, `official_discord_invite`,
   `youtube.playlist_id`, Discord/Reddit ids) as TODO until known.
2. **`config/settings.yaml`** — add `description_templates.default_<slug>`
   and `hashtag_lines.default_<slug>` (the only per-game copy the renderer
   can't derive).
3. **Premiere template** — `assets/premiere_templates/<slug>_nest.prproj`.
   **Build it by DUPLICATING `lom_nest.prproj`**, not from `guideline.prproj`:
   the nest approach (gameplay lives in a `GAMEPLAY_NEST` sub-sequence dropped
   on V7 as one clip, with the 3 tuned Ultra Keys on that nest clip) comes for
   free. Re-skin decor/overlays; keep the track ROLES matching the yaml. If
   the inner sequence is renamed, update `sequence_name`. If the game gets a
   pre-recorded Aptoide ad later, set `promo.present: true`, drop `PROMO`
   clips on V7 + the voice track, put `<slug>.mp4` in `assets/aptoide_ads/`,
   and run `scripts/jsx/describe_project.jsx` once to emit the promo geometry
   dump.
4. **Photoshop templates** — drop ≥1 `.psd` (≥2 text Smart Objects, 16:9
   canvas) in `assets/photoshop_templates/<slug>/`. Rotation is automatic.
   Each text Smart Object must be **top-level on the canvas** (so the renderer
   finds it) and hold a text layer. The renderer captures the PLACEHOLDER
   text's bounds as the "design box" (ground truth for the visible, un-clipped
   region — the SO's own canvas can map off-screen, so it's the only reliable
   frame), then **scales the new copy to FILL that box** — growing short copy
   and shrinking long copy (via layer resize, NOT `textItem.size`, whose px/pt
   units are unreliable inside an SO) — and re-anchors it. So text fills the
   space without clipping, overlap, or crushing the art beside it. Thumbnail
   copy is **hard-capped to 3 words** (first word → top SO, rest → bottom);
   fewer words render larger. Tune per game via `photoshop_template`:
   `autofit_text` / `text_fit_margin` (box padding) / `text_fit_min_scale`
   (shrink floor; below it the copy is shortened + re-rendered, then flagged).
5. **No code changes needed** — research/topics/script/metadata/cut/render/
   thumb/upload/social all resolve per-game from config. (The two former
   hardcodes — the description Discord invite and recent-uploads keywords —
   are now data-driven: `sponsorship.official_discord_invite` and
   `display_name`+`slug` respectively.)

**Gameplay audio is muxed post-render, NOT placed in the .prproj.** In this
Premiere version every cloned audio cluster de-dupes to its blueprint
recording at render time no matter how the XML identity is sanitized, so
`rebuild` writes `<slug>_gameplay_audio.wav` (ffmpeg-concatenated keep
segments) and `yta render-video` splices it into the AME-exported mp4 via
`adobe/audio_mux.py`. Applies to every game — a new template needs no
gameplay-voice track wiring (its voice/music track roles still matter for
the promo + music).

## Useful one-liners

```bash
# Activate venv (project-relative)
source .venv/bin/activate                # Mac
.venv\Scripts\activate                   # Windows

# Run tests
pytest

# End-to-end for a fresh video
yta research lom
yta topics lom --n 5
yta script lom --topic 0                 # 0 = top-ranked candidate
yta metadata lom --n 3
yta upload lom --video '...' --thumbnail '...' --title-index 0 --privacy private --yes
```

## Repo & commit policy

- GitHub: <https://github.com/joranro1997/YoutubeAutomator>
- Default branch: `main`. Feature branch: `claude/pedantic-kowalevski`.
- The user is the only committer; treat `main` as personal trunk.
- Always run `pytest` before committing. The 3 smoke tests must stay green.
