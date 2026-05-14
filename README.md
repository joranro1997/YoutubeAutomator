# YoutubeAutomator

Automation pipeline for a sponsored mobile-game YouTube channel covering
**Legend of Mushroom** and **Legend of Elements** (both under Aptoide Connect
Affiliate Agreements).

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

## Status

Skeleton stage. Module stubs document intent; implementations land per phase:
- **Phase 1** (current focus): research → topics → script → metadata. Mac-friendly.
- **Phase 2**: YouTube upload.
- **Phase 3**: Adobe automation on Windows.
