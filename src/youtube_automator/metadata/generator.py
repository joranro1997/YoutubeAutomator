"""YouTube metadata generation: title variants, description, tags.

The description template comes verbatim from the user's real videos (see
settings.yaml description_templates) — only the per-video {hook} and
{timestamps} are produced fresh. Everything else (affiliate code, links,
bio, hashtags, social links) is filled from config.

Outputs N title/thumbnail-copy variants for the user to pick. Each is
ranked by an expected_ctr_rationale Claude provides.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from ..config import GameConfig, get_settings
from ..llm.claude import SystemBlock, complete
from ..script.generator import Script
from ..script.guardrails import GuardrailViolation, check_description


_log = logging.getLogger(__name__)


class MetadataCandidate(BaseModel):
    title: str
    thumbnail_copy: str        # the big text overlay for the thumbnail
    expected_ctr_rationale: str


class VideoMetadata(BaseModel):
    candidates: list[MetadataCandidate]
    description: str           # rendered, contract-compliant
    tags: list[str] = Field(default_factory=list)
    description_violations: list[str] = Field(default_factory=list)


_SYSTEM_INSTRUCTIONS = """You are generating title + thumbnail-copy options for a sponsored mobile-game YouTube video on @MidwayPaladin's channel (high-energy English gaming voice).

Output STRICT JSON, no prose:
[
  {
    "title": "punchy YouTube title (<= 70 chars when possible)",
    "thumbnail_copy": "<= 6 word big-text overlay for the thumbnail",
    "expected_ctr_rationale": "one sentence on why this works"
  }
]

Rules:
1. Titles MUST match @MidwayPaladin's style: lots of caps for emphasis, hooks like "INSANE", "MASSIVE", "BROKEN", "STOP X", "DO THIS"; rhetorical questions; numbers; "(Legend of <Game>)" suffix is common but not required.
2. Each title should drive both CTR and affiliate-code conversion intent (event/spending/gacha/update topics convert better than lore).
3. Thumbnail copy is bigger and shorter than the title — pick 3-6 words that POP.
4. Do NOT mention Aptoide in the title or thumbnail copy.
5. Do NOT use clickbait that the video doesn't deliver on.
"""


_JSON_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def _extract_json_array(text: str) -> str:
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        raise ValueError(f"Metadata response had no JSON array:\n{text[:500]}")
    return m.group(0)


def _build_hook(script: Script) -> str:
    """Hook for the description: 1-3 lines summarizing what the video delivers."""
    # Use the topic angle as the spine, expanded with a couple of cues from the intro.
    intro_text = next((s.text for s in script.segments if s.kind == "intro"), "")
    # Trim intro to first ~2 sentences to keep description tight.
    sentences = re.split(r"(?<=[.!?])\s+", intro_text.strip())
    seed = " ".join(sentences[:2]).strip()
    if seed:
        return seed
    return script.topic.angle


def _build_timestamps(script: Script) -> str:
    cursor = 0
    lines: list[str] = []
    for s in script.segments:
        if s.kind == "aptoide_ad_marker":
            cursor += s.duration_s_estimate
            continue
        mins, secs = divmod(cursor, 60)
        label = {"intro": "Intro", "topic": s.shot_notes[:40] or "Main content", "outro": "Outro"}.get(
            s.kind, s.kind.title()
        )
        # Strip newlines from the label so timestamps stay one-per-line.
        label = label.replace("\n", " ").strip()
        lines.append(f"{mins:02d}:{secs:02d} — {label}")
        cursor += s.duration_s_estimate
    return "\n".join(lines)


def _render_description(
    game: GameConfig,
    script: Script,
    hook: str,
    timestamps: str,
) -> str:
    settings = get_settings()
    tmpl_id = game.sponsorship.description_template_id
    template = settings.description_templates.get(tmpl_id, "")
    if not template:
        _log.warning("no description template %r; using empty fallback", tmpl_id)
        return ""
    hashtags = settings.hashtag_lines.get(tmpl_id, "")

    # Identify the game's own Discord (skip Aptoide's — it has its own link block).
    game_official = ""
    for g in game.sources.discord.guilds:
        if "aptoide" not in g.label.lower():
            game_official = f"https://discord.gg/{g.guild_id}"  # placeholder; templates
            # User templates use a public invite URL; we don't have an invite ID per
            # se. Fall back to the well-known invite tokens used in the real templates:
            if game.slug == "lom":
                game_official = "https://discord.gg/lom"
            elif game.slug == "loe":
                game_official = "https://discord.gg/loe"
            break

    return template.format(
        affiliate_code=game.sponsorship.affiliate_code,
        download_link=game.sponsorship.download_link,
        browser_play_link=game.sponsorship.browser_play_link,
        promo_video_url=game.sponsorship.promo_video_url,
        game_official_discord=game_official,
        hook=hook,
        timestamps=timestamps,
        creator_twitch=settings.channel.links.twitch,
        creator_twitter=settings.channel.links.twitter,
        creator_discord=settings.channel.links.discord,
        creator_bio=settings.channel.creator_bio.strip(),
        hashtags=hashtags,
    )


def _build_tags(game: GameConfig, script: Script) -> list[str]:
    tags = list(game.youtube.tag_seeds)
    # Add topic-specific phrases extracted from the title hook (lowercased, deduped).
    candidate = script.topic.title_hook.lower()
    for kw in re.findall(r"[a-z]{4,}", candidate):
        if kw not in {"with", "this", "your", "that", "from", "have", "just", "more", "what",
                      "when", "where", "they", "them", "into", "about", "their", "would"}:
            if kw not in tags:
                tags.append(kw)
    return tags[:40]   # YouTube hard-caps at 500 chars total; 40 short tags is comfortably under


def generate(
    game: GameConfig,
    script: Script,
    n_titles: int = 3,
    style_excerpt: str = "",
) -> VideoMetadata:
    """Generate metadata for an approved script."""
    user_msg = (
        f"Game: {game.display_name}\n"
        f"Topic: {script.topic.title_hook}\n"
        f"Angle: {script.topic.angle}\n"
        f"Intro segment (first ~30s of spoken script):\n"
        f"  {next((s.text for s in script.segments if s.kind == 'intro'), '')[:600]}\n\n"
        f"Generate {n_titles} title + thumbnail-copy variants. Return JSON array."
    )
    system_blocks: list[SystemBlock] = [SystemBlock(_SYSTEM_INSTRUCTIONS, cacheable=False)]
    if style_excerpt:
        system_blocks.append(SystemBlock(style_excerpt, cacheable=True))

    resp = complete(
        "metadata_generation",
        system=system_blocks,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.8,
        max_tokens=2000,
    )

    arr = json.loads(_extract_json_array(resp.text))
    candidates: list[MetadataCandidate] = []
    for obj in arr:
        try:
            candidates.append(MetadataCandidate.model_validate(obj))
        except ValidationError as e:
            _log.warning("skipping malformed metadata candidate: %s", e)

    hook = _build_hook(script)
    timestamps = _build_timestamps(script)
    description = _render_description(game, script, hook, timestamps)
    tags = _build_tags(game, script)

    violations: list[GuardrailViolation] = check_description(description, game)
    if violations:
        for v in violations:
            _log.warning("description guardrail: %s — %s", v.rule, v.detail)

    _log.info(
        "metadata_generation: in=%d out=%d cache_read=%d cache_write=%d candidates=%d",
        resp.input_tokens, resp.output_tokens,
        resp.cache_read_input_tokens, resp.cache_creation_input_tokens,
        len(candidates),
    )
    return VideoMetadata(
        candidates=candidates,
        description=description,
        tags=tags,
        description_violations=[f"{v.rule}: {v.detail}" for v in violations],
    )
