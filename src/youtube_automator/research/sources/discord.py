"""Discord source for the official game server.

User has personal access to the LoM official Discord but no bot. Two viable
approaches; pick at integration time:

1. Manual-assisted (recommended, ToS-safe):
   - User periodically pastes raw text of #announcements / #patch-notes channels
     into a watched file (e.g. data/research_cache/discord_<game>_inbox.md).
   - This module parses that file into ResearchItems.

2. Self-bot via user token (FAST but violates Discord ToS — account ban risk):
   - Not recommended; included only for completeness.

Default implementation will go with (1).
"""

from __future__ import annotations

from ..types import ResearchItem
from ...config import GameConfig


def fetch(game: GameConfig) -> list[ResearchItem]:
    """Parse the manual-paste inbox file for this game.

    NOTE: stub. Will read data/research_cache/discord_<slug>_inbox.md,
    split by '---' delimiters, extract date/title/body/channel, return items.
    """
    raise NotImplementedError("Stub — implement manual-paste parser")
