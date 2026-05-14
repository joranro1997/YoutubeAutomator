"""Topic generation: ranks candidate video topics from the research snapshot.

Asks Claude (task="topic_selection") to propose N topics with:
- title hook
- 1-line angle
- expected appeal (CTR-leaning vs. evergreen guide)
- expected affiliate conversion potential (events / monetization-relevant topics
  convert better than pure lore content)
- the source items it grounds in (URLs) — for §4.5 verifiability
"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import GameConfig
from ..research.types import ResearchItem


class TopicCandidate(BaseModel):
    title_hook: str
    angle: str
    appeal_score: int          # 1-10, hand-wave CTR potential
    conversion_score: int      # 1-10, likely affiliate code conversions
    grounding_urls: list[str]  # source URLs that back this topic
    rationale: str             # why Claude thinks this works


def propose(game: GameConfig, items: list[ResearchItem], n: int = 5) -> list[TopicCandidate]:
    """Return up to N topic candidates ranked best-first.

    NOTE: stub. Will summarize items, prompt Claude for ranked topics with
    grounding URLs, validate JSON output, return TopicCandidate list.
    """
    raise NotImplementedError("Stub — implement topic generation prompt")
