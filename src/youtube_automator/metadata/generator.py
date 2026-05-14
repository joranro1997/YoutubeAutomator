"""YouTube metadata generation: title variants, description, tags.

Produces N candidates for title and thumbnail copy so the user picks the
winner. Description is rendered from the game's template in settings.yaml
with the affiliate code and timestamps filled in.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..config import GameConfig
from ..script.generator import Script


class MetadataCandidate(BaseModel):
    title: str
    thumbnail_copy: str        # the big text overlay for the thumbnail
    expected_ctr_rationale: str


class VideoMetadata(BaseModel):
    candidates: list[MetadataCandidate]
    description: str           # rendered, contract-compliant
    tags: list[str] = Field(default_factory=list)


def generate(game: GameConfig, script: Script, n_titles: int = 3) -> VideoMetadata:
    """Generate metadata for an approved script.

    NOTE: stub. Will:
      1. Ask Claude for N title/thumbnail copy variants with rationales.
      2. Render the description template with affiliate_code + timestamps
         derived from script segment durations.
      3. Compose tags from game.youtube.tag_seeds + topic-specific terms.
      4. Run guardrails.check_description before returning.
    """
    raise NotImplementedError("Stub — implement metadata generation")
