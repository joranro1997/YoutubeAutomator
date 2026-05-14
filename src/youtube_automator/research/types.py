"""Shared data types for the research layer.

Every source (reddit, discord, web, ...) produces a list of `ResearchItem`s
that the aggregator deduplicates and feeds into topic generation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["reddit", "discord", "patch_notes", "wiki", "web"]


class ResearchItem(BaseModel):
    """A single piece of research evidence about the game.

    Must be source-attributable: per Aptoide contract §4.5 every factual claim
    in a script must trace back to a verified source.
    """

    source: SourceKind
    source_url: str = ""
    source_label: str = ""               # e.g. "r/LegendOfMushroom" or "patch notes 1.42"
    title: str
    body: str = ""
    author: str = ""
    score: int | None = None             # upvotes / reactions if available
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    posted_at: datetime | None = None
    game_slug: str
    tags: list[str] = Field(default_factory=list)
