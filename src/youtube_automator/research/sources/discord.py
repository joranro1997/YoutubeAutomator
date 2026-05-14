"""Discord source: manual-paste parser (Discord ToS-safe).

Why manual-paste: the user has personal access to the official LoM, LoE and
Aptoide Discords but no bot. Automated scraping via a user token (self-bot)
violates Discord ToS and risks the user's account. Inviting a real bot may
not be possible in those servers. Solution: the user periodically copy-pastes
the relevant channel(s) contents into a watched file, and this parser ingests
them as ResearchItems.

Inbox file: data/research_cache/discord_<slug>_inbox.md

Format (lenient — the parser tolerates Discord's copy-paste output):
    ## <channel-name>     # optional channel header
    ---
    [yyyy-mm-dd HH:MM] author: message text...
    [yyyy-mm-dd HH:MM] author: another message...
    ---
    ## <other-channel>
    [yyyy-mm-dd] author: ...

Each `[...] author: ...` block becomes one ResearchItem. The `---` and `##`
delimiters are optional but help the parser tag items with the right channel.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from ...config import GameConfig
from ...paths import RESEARCH_CACHE_DIR, ensure_dirs
from ..types import ResearchItem


_log = logging.getLogger(__name__)

# Matches the standard "[<time>] <author>" header at the start of a line.
# Date forms tolerated: 2026-05-11 14:32, 2026/05/11, 11/05/2026, May 11, 14:32.
_MSG_HEADER = re.compile(
    r"""^\s*\[?\s*
        (?P<ts>[^\]]+?)
        \s*\]?\s+
        (?P<author>[^:]{1,80}?)
        \s*:\s*
        (?P<body>.*)$
    """,
    re.VERBOSE,
)

_CHANNEL_HEADER = re.compile(r"^\s*##\s*(?P<channel>.+?)\s*$")
_DELIMITER = re.compile(r"^\s*---+\s*$")


def inbox_path(game: GameConfig) -> Path:
    return RESEARCH_CACHE_DIR / f"discord_{game.slug}_inbox.md"


def _try_parse_ts(s: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%b %d %H:%M",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch(game: GameConfig) -> list[ResearchItem]:
    ensure_dirs()
    path = inbox_path(game)
    if not path.exists():
        _log.info("no discord inbox at %s — paste channel contents to enable", path)
        return []

    items: list[ResearchItem] = []
    current_channel = ""
    pending: dict | None = None

    def flush():
        if pending and pending.get("body"):
            items.append(
                ResearchItem(
                    source="discord",
                    source_url="",
                    source_label=f"Discord #{current_channel}" if current_channel else "Discord",
                    title=pending["title"],
                    body=pending["body"].strip(),
                    author=pending["author"],
                    score=None,
                    posted_at=pending["ts"],
                    game_slug=game.slug,
                    tags=[current_channel] if current_channel else [],
                )
            )

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if _DELIMITER.match(line):
            flush()
            pending = None
            continue
        m_ch = _CHANNEL_HEADER.match(line)
        if m_ch:
            flush()
            pending = None
            current_channel = m_ch.group("channel").strip()
            continue
        m = _MSG_HEADER.match(line)
        if m:
            flush()
            ts = _try_parse_ts(m.group("ts"))
            body = m.group("body").strip()
            pending = {
                "ts": ts,
                "author": m.group("author").strip(),
                "title": (body[:120] + ("…" if len(body) > 120 else "")) or "(no title)",
                "body": body,
            }
            continue
        # Continuation of the current message body.
        if pending is not None and line.strip():
            pending["body"] = (pending["body"] + "\n" + line).strip()

    flush()
    return items
