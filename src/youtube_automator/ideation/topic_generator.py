"""Topic generation: ranks candidate video topics from the research snapshot.

Asks Claude (task="topic_selection") to propose N topics with:
- title_hook: a punchy, hooky title in the user's voice
- angle: 1-line angle
- appeal_score: 1-10 — expected CTR / virality
- conversion_score: 1-10 — likelihood viewers will click the affiliate code
  (events, gachas, monetization-relevant updates convert better than pure
  lore content)
- grounding_urls: source URLs that back this topic (every factual claim
  must trace back to a source — Aptoide contract §4.5)
- rationale: why Claude thinks this works

Returns TopicCandidate[] sorted best-first.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from pydantic import BaseModel, Field

from ..config import GameConfig
from ..llm.claude import SystemBlock, complete
from ..research.types import ResearchItem


_log = logging.getLogger(__name__)


class TopicCandidate(BaseModel):
    title_hook: str
    angle: str
    appeal_score: int = Field(ge=1, le=10)
    conversion_score: int = Field(ge=1, le=10)
    grounding_urls: list[str] = Field(default_factory=list)
    rationale: str


_SYSTEM_INSTRUCTIONS = """You are a strategy partner for a sponsored mobile-game YouTuber (channel @MidwayPaladin, 100% English, high-energy gaming voice).

Your job: given recent research signal about a game, propose ranked video topics that:
1. Have a strong chance of high CTR (hooky title, current event, FOMO).
2. Drive affiliate-code conversions — events, gachas, big updates, deals, beginner/returning-player onboarding all convert well; pure lore/opinion does not.
3. Are grounded in the provided source items (every factual claim must trace to a `source_url`).
4. Are NOT topics the creator just covered (avoid near-duplicates of his own recent uploads).

Output STRICT JSON, no prose:
[
  {
    "title_hook": "...",
    "angle": "one-line video angle",
    "appeal_score": 1-10,
    "conversion_score": 1-10,
    "grounding_urls": ["https://..."],
    "rationale": "why this wins"
  }
]
"""


def _compact_research(items: list[ResearchItem], max_chars: int = 18000) -> str:
    """Group items by source and emit a compact list Claude can ground on."""
    by_source: dict[str, list[ResearchItem]] = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)

    parts: list[str] = []
    used = 0
    for source, group in by_source.items():
        head = f"\n### Source: {source} ({len(group)} items)\n"
        parts.append(head)
        used += len(head)
        for it in group:
            score = f" (score={it.score})" if it.score is not None else ""
            posted = f" [{it.posted_at.strftime('%Y-%m-%d')}]" if it.posted_at else ""
            body = (it.body or "").replace("\n", " ").strip()
            if len(body) > 300:
                body = body[:300] + "…"
            entry = (
                f"- [{it.source_label}]{posted}{score} "
                f"{it.title.strip()} — {body}\n  url: {it.source_url}\n"
            )
            if used + len(entry) > max_chars:
                parts.append("\n(…truncated…)\n")
                return "".join(parts)
            parts.append(entry)
            used += len(entry)
    return "".join(parts)


_JSON_BLOCK_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def _extract_json_array(text: str) -> str:
    """Tolerant: strip leading markdown fences / preamble around a JSON array."""
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        raise ValueError(f"Claude response did not contain a JSON array:\n{text[:500]}")
    return m.group(0)


def propose(
    game: GameConfig,
    items: list[ResearchItem],
    n: int = 5,
    style_excerpt: str = "",
) -> list[TopicCandidate]:
    """Return up to N topic candidates ranked best-first."""
    if not items:
        _log.warning("no research items for %s — cannot propose topics", game.slug)
        return []

    research_block = _compact_research(items)

    user_msg = (
        f"Game: {game.display_name}\n"
        f"Affiliate code: {game.sponsorship.affiliate_code}\n"
        f"Number of topics to propose: {n}\n\n"
        f"Recent research signal:\n{research_block}\n\n"
        f"Return STRICT JSON array of {n} topic candidates, best first."
    )

    system_blocks: list[SystemBlock] = [SystemBlock(_SYSTEM_INSTRUCTIONS, cacheable=False)]
    if style_excerpt:
        # Style corpus is large and reused across calls in a session — cache it.
        system_blocks.append(SystemBlock(style_excerpt, cacheable=True))

    resp = complete(
        "topic_selection",
        system=system_blocks,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.7,
        max_tokens=3000,
    )

    raw = _extract_json_array(resp.text)
    arr = json.loads(raw)
    out: list[TopicCandidate] = []
    for obj in arr:
        try:
            out.append(TopicCandidate.model_validate(obj))
        except Exception as e:  # noqa: BLE001
            _log.warning("skipping malformed candidate: %s", e)

    out.sort(key=lambda c: (c.appeal_score + c.conversion_score), reverse=True)
    _log.info(
        "topic_selection: in=%d out=%d cache_read=%d cache_write=%d",
        resp.input_tokens,
        resp.output_tokens,
        resp.cache_read_input_tokens,
        resp.cache_creation_input_tokens,
    )
    return out
