"""Script generator.

Produces a video script in the user's voice, grounded in the selected topic's
research items. Output is a structured script aligned to the user's existing
Premiere template segments:

    intro -> topic block(s) -> aptoide_ad (pre-recorded splice point) -> outro

Hard requirements (enforced post-generation by guardrails.py):
- mentions Aptoide at least once verbally (§4 Appendix)
- includes the affiliate code call-to-action
- every factual claim about the game cites a grounding URL (§4.5)
- never claims Aptoide hosts paid apps for free (§4.11)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..config import GameConfig
from ..ideation.topic_generator import TopicCandidate
from ..research.types import ResearchItem


class ScriptSegment(BaseModel):
    kind: str                  # "intro" | "topic" | "aptoide_ad_marker" | "outro"
    text: str                  # spoken text (None for ad marker)
    shot_notes: str = ""       # what gameplay to show during this segment
    duration_s_estimate: int = 0
    citations: list[str] = Field(default_factory=list)  # URLs grounding factual claims


class Script(BaseModel):
    game_slug: str
    topic: TopicCandidate
    segments: list[ScriptSegment]
    total_duration_s_estimate: int = 0


def generate(
    game: GameConfig,
    topic: TopicCandidate,
    items: list[ResearchItem],
) -> Script:
    """Generate a full script for the chosen topic, grounded in `items`.

    NOTE: stub. Will:
      1. Build system prompt with style_corpus + contract guardrails.
      2. Pass topic + grounding items + Aptoide insertion rules.
      3. Ask Claude for structured JSON output.
      4. Validate; return Script.
    """
    raise NotImplementedError("Stub — implement script generation prompt")
