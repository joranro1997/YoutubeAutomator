"""Script generator.

Produces a structured video script in the user's voice, grounded in the
selected topic's research items.

Output structure (aligned to the user's existing Premiere template):

    [intro]                    # greeting, energy setter, "today we cover X"
    [aptoide_ad_marker]        # placeholder where his pre-recorded ad clip slices in
    [topic block(s)]           # the actual content, factual claims with citations
    [outro]                    # thanks for watching, like/sub, affiliate code CTA

Contractual requirements baked into the prompt and enforced by
script/guardrails.py:
- At least one verbal mention of "Aptoide" is satisfied by the
  aptoide_ad_marker segment (which represents the pre-recorded clip).
- Every factual claim about the game cites a grounding_url from the
  research items (§4.5: verifiable opinions/facts).
- The §4.11 banned phrasing about Aptoide hosting paid apps for free is
  never produced.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from ..config import GameConfig
from ..ideation.topic_generator import TopicCandidate
from ..llm.claude import SystemBlock, complete
from ..research.types import ResearchItem


_log = logging.getLogger(__name__)


class ScriptSegment(BaseModel):
    kind: str                  # "intro" | "topic" | "aptoide_ad_marker" | "outro"
    text: str = ""             # spoken text (empty for ad marker)
    shot_notes: str = ""       # what gameplay to show during this segment
    duration_s_estimate: int = 0
    citations: list[str] = Field(default_factory=list)  # URLs grounding factual claims


class Script(BaseModel):
    game_slug: str
    topic: TopicCandidate
    segments: list[ScriptSegment]
    total_duration_s_estimate: int = 0


_SYSTEM_INSTRUCTIONS = """You are writing a YouTube video script for the channel @MidwayPaladin (high-energy English gaming voice). The creator records his own voice over your script; you are NOT writing a voice-over for an AI to read.

Output a STRICT JSON array of segments. Each segment is one object:
{
  "kind": "intro" | "topic" | "aptoide_ad_marker" | "outro",
  "text": "spoken text",
  "shot_notes": "what gameplay/B-roll/overlays to show",
  "duration_s_estimate": <int seconds>,
  "citations": ["https://..."]   // URLs grounding factual claims in this segment
}

Hard rules:
1. The first segment MUST be "intro" — a 15–30 second greeting/hook setting up the topic.
2. The second segment MUST be "aptoide_ad_marker" with EMPTY "text" (the creator splices in a pre-recorded Aptoide ad clip here). Set "duration_s_estimate" to ~30. Use shot_notes to label the splice point.
3. Then 2–4 "topic" segments delivering the actual content. Each factual claim about the game (a number, a date, a mechanic, an event, a balance change, etc.) MUST cite a source URL from the research items provided. If you do not have a source for a claim, OMIT the claim. Do not invent stats.
4. Final segment MUST be "outro" — 20–40 seconds, thanks/like/sub, and an explicit verbal CTA to use the affiliate code on Aptoide for a 5% bonus.
5. Total length should land between 7 and 12 minutes (420–720 seconds).
6. NEVER say or imply that Aptoide is a place where paid apps can be downloaded for free. NEVER disparage Aptoide.
7. Match the creator's actual voice (excerpts provided): high-energy, casual, lots of hooks ("INSANE", "MASSIVE", "BROKEN", "CRAZY"), direct audience-address ("you guys", "let me tell you"). Avoid corporate or AI-stilted phrasing.
8. Return ONLY the JSON array. No prose before or after.
"""


def _grounding_block(items: list[ResearchItem], topic: TopicCandidate, max_chars: int = 14000) -> str:
    """Build a compact research block. Topic.grounding_urls are listed first."""
    prioritized: list[ResearchItem] = []
    rest: list[ResearchItem] = []
    grounding_set = set(topic.grounding_urls or [])
    for it in items:
        (prioritized if it.source_url in grounding_set else rest).append(it)
    ordered = prioritized + rest

    lines: list[str] = []
    used = 0
    for it in ordered:
        body = (it.body or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"
        posted = f" [{it.posted_at.strftime('%Y-%m-%d')}]" if it.posted_at else ""
        score = f" (score={it.score})" if it.score is not None else ""
        entry = (
            f"- [{it.source}/{it.source_label}]{posted}{score} "
            f"{it.title.strip()} — {body}\n  url: {it.source_url}\n"
        )
        if used + len(entry) > max_chars:
            lines.append("\n(…research truncated…)\n")
            break
        lines.append(entry)
        used += len(entry)
    return "".join(lines)


_JSON_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def _extract_json_array(text: str) -> str:
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        raise ValueError(f"Script response had no JSON array:\n{text[:500]}")
    return m.group(0)


def generate(
    game: GameConfig,
    topic: TopicCandidate,
    items: list[ResearchItem],
    style_excerpt: str = "",
) -> Script:
    """Generate a full script for the chosen topic, grounded in `items`."""
    grounding = _grounding_block(items, topic)

    user_msg = (
        f"Game: {game.display_name}\n"
        f"Affiliate code (must be mentioned in the outro CTA): "
        f"{game.sponsorship.affiliate_code}\n"
        f"Download link: {game.sponsorship.download_link}\n"
        f"Browser-play link: {game.sponsorship.browser_play_link or '(none)'}\n\n"
        f"Selected topic:\n"
        f"  title_hook: {topic.title_hook}\n"
        f"  angle: {topic.angle}\n"
        f"  rationale: {topic.rationale}\n"
        f"  grounding_urls (prioritized): {topic.grounding_urls}\n\n"
        f"Research items (cite by url for every factual claim):\n{grounding}\n\n"
        f"Return the JSON array now."
    )

    system_blocks: list[SystemBlock] = [SystemBlock(_SYSTEM_INSTRUCTIONS, cacheable=False)]
    if style_excerpt:
        system_blocks.append(SystemBlock(style_excerpt, cacheable=True))

    resp = complete(
        "script_generation",
        system=system_blocks,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.8,
        max_tokens=6000,
    )

    raw = _extract_json_array(resp.text)
    arr = json.loads(raw)
    segments: list[ScriptSegment] = []
    for obj in arr:
        try:
            segments.append(ScriptSegment.model_validate(obj))
        except ValidationError as e:
            _log.warning("skipping malformed segment: %s", e)

    total = sum(s.duration_s_estimate for s in segments)
    _log.info(
        "script_generation: in=%d out=%d cache_read=%d cache_write=%d total_dur=%ds segments=%d",
        resp.input_tokens, resp.output_tokens,
        resp.cache_read_input_tokens, resp.cache_creation_input_tokens,
        total, len(segments),
    )
    return Script(
        game_slug=game.slug,
        topic=topic,
        segments=segments,
        total_duration_s_estimate=total,
    )
