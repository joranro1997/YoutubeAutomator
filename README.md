# YoutubeAutomator

Automation pipeline for a sponsored mobile-game YouTube channel covering
**Legend of Mushroom**, **Legend of Elements** and **Duck Survival** (all
under Aptoide Connect Affiliate Agreements).

## What it does

1. **Research** — pulls fresh material from Reddit, the official Discord (manual paste), patch notes and wikis.
2. **Topic ideation** — Claude proposes ranked video topics, each grounded in source URLs.
3. **Script** — Claude writes a script in the user's voice (RAG over his prior transcripts), with hard contract guardrails baked in (Aptoide mention, affiliate code CTA, no hallucinations about the game).
4. **Metadata** — title/thumbnail copy variants + a contract-compliant description.
5. **Render** *(Windows only)* — drives Adobe Premiere and Photoshop via ExtendScript using the user's existing templates.
6. **Upload** — pushes to YouTube via the Data API v3 (private by default; the user flips to public/scheduled after a final eyeball).

What the pipeline does **not** do, and why:
- **Voice**: the user records his own voice. Cloning is contractually risky (§4.5 honesty + §11 grants Aptoide perpetual rights over his voice).
- **Gameplay**: the user records actual gameplay. Synthetic gameplay would breach §4.2 (originality) and §4.5 (truthful experiences).

See `memory/project_contract.md` (in `.claude/projects/`) for the full constraint list.

## Layout

```
config/
  settings.yaml          # channel-wide settings + contract guardrails
  games.yaml             # per-game sources, sponsorship, YouTube defaults
src/youtube_automator/
  config.py paths.py     # cross-platform config + path resolution
  llm/                   # Claude API wrapper
  research/              # sources (reddit, discord, web) + aggregator
  ideation/              # topic candidate generation
  script/                # script generation + contract guardrails
  metadata/              # title/description/tags
  transcribe/            # one-off style-corpus builder (yt-dlp + whisper)
  upload/                # YouTube Data API v3 client
  adobe/                 # Premiere + Photoshop ExtendScript (Windows-only)
  cli.py                 # `yta` command
scripts/                 # entrypoints for cron / manual runs
data/                    # corpus, research snapshots, outputs (gitignored)
tests/
```

## Setup

Dev on Mac, production on Windows. Code is Python 3.11+ and cross-platform; only the `adobe/` module is Windows-only at runtime.

```bash
# Both Mac and Windows:
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # then fill in API keys
```

Run smoke tests:
```bash
pytest
```

## Moving to a new Windows PC

The code is **path-portable**: clone it anywhere, under any username. Adobe
executables are auto-detected under `Program Files\Adobe\` (preferring the
required versions — Premiere 2020, **AME 2020**, **Photoshop 2021**), and the
ExtendScript helpers derive the repo path from their own location or are
rewritten on install. Anything machine-specific is overridable via `.env`.

What git does **NOT** carry (gitignored) — copy these from the old PC:

| Path | What | Notes |
|---|---|---|
| `.env` | API keys + optional path overrides | Anthropic, Discord bot+webhook, Twitter, optional `PREMIERE_EXE`/`AME_EXE`/`PHOTOSHOP_EXE`/`YTA_RENDER_PRESET` |
| `secrets/client_secret.json` | YouTube OAuth client | from Google Cloud Console |
| `secrets/youtube_token.json` | cached YouTube token | optional — first upload re-auths in the browser if missing/expired |
| `assets/premiere_templates/*.prproj` | the nest templates (`lom_nest`, `loe`, `dsv_nest`) | required to render |
| `assets/photoshop_templates/<slug>/*.psd` | thumbnail templates | required for `render-thumb` |
| `assets/aptoide_ads/*.mp4` | pre-recorded promo clips (LoM) | required only for games with a promo block |
| `data/corpus/transcripts/*` | style corpus | recommended — scripts read your voice from here |

Steps on the new PC:

```bash
git clone https://github.com/joranro1997/YoutubeAutomator
cd YoutubeAutomator
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
# copy the gitignored items above into place, then:
copy .env.example .env     # fill in keys (or copy your old .env)
pytest                     # smoke check (50 tests)
```

Then on Windows, one-time Adobe wiring:
1. Install **Adobe Premiere Pro 2020**, **Adobe Media Encoder 2020**,
   **Photoshop 2021** (same versions). Standard `Program Files\Adobe\`
   install ⇒ auto-detected. Non-standard ⇒ set `PREMIERE_EXE` / `AME_EXE` /
   `PHOTOSHOP_EXE` in `.env`.
2. `winget install Gyan.FFmpeg` (auto-detected; or put it on PATH).
3. Import the `yta_render.epr` AME preset (Media Encoder ▸ Preset Browser ▸
   Import), or point `YTA_RENDER_PRESET` at it.
4. `yta install-cep` — installs the Premiere render panel and rewrites it to
   this machine's repo path.
5. Desktop shortcut to `.venv\Scripts\yta-gui.exe` to launch the GUI.

## Status

Phases 1–3 complete and in daily use:
- **Phase 1**: research → topics → script → metadata (Claude, contract guardrails).
- **Phase 2**: YouTube upload (OAuth, scheduled publish, thumbnail, playlist).
- **Phase 3**: Adobe automation on Windows (offline `.prproj` rebuild + AME
  export + post-render audio mux; Photoshop thumbnails).
