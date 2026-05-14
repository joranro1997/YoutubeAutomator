"""Discord source: two ToS-safe ingestion paths.

(1) BOT path — for upstream Announcement channels followed into the user's
    own server. The user invites their own bot to that server with
    View Channel + Read Message History permissions, then runs the pipeline.
    We hit the Discord REST API directly (no discord.py dependency) to fetch
    the N most recent messages from each mirror channel.

(2) MANUAL-PASTE path — for upstream channels that are NOT followable
    (e.g. creators-announce, dev-feedback). The user pastes copied content
    into data/research_cache/discord_<slug>_inbox.md and the parser ingests
    it the same way as before. See `yta paste-discord <game>` CLI helper.

Both paths return ResearchItem[] with source="discord".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ...config import GameConfig, get_env
from ...paths import RESEARCH_CACHE_DIR, ensure_dirs
from ..types import ResearchItem


_log = logging.getLogger(__name__)


# ---------- BOT path ------------------------------------------------------- #


_DISCORD_API = "https://discord.com/api/v10"


def _fetch_channel_messages(channel_id: str, token: str, limit: int) -> list[dict]:
    """GET /channels/{id}/messages?limit=N. Returns newest first."""
    headers = {"Authorization": f"Bot {token}", "User-Agent": "YoutubeAutomator (linux, 0.1)"}
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=headers, params={"limit": min(limit, 100)})
    if r.status_code == 401:
        raise RuntimeError("Discord bot token rejected (401). Check DISCORD_BOT_TOKEN.")
    if r.status_code == 403:
        raise RuntimeError(
            f"Discord bot lacks permission on channel {channel_id} (403). "
            "Ensure the bot is in your server with View Channel + Read Message History."
        )
    if r.status_code == 404:
        raise RuntimeError(f"Discord channel {channel_id} not found (404).")
    r.raise_for_status()
    return r.json()


def _msg_to_item(msg: dict, game_slug: str, channel_label: str) -> ResearchItem:
    content = msg.get("content") or ""
    # Follow'd messages typically carry the original channel/server name in
    # the message's embeds or webhook author.
    author = (msg.get("author") or {}).get("username", "")
    ts_raw = msg.get("timestamp")
    ts: datetime | None = None
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            ts = None

    # Append a compact rendering of any embeds (titles + descriptions).
    embed_parts: list[str] = []
    for e in msg.get("embeds") or []:
        title = e.get("title")
        desc = e.get("description")
        if title:
            embed_parts.append(f"**{title}**")
        if desc:
            embed_parts.append(desc)
    body = content
    if embed_parts:
        body = (body + "\n" + "\n".join(embed_parts)).strip()

    title_text = body.splitlines()[0] if body else "(empty message)"
    if len(title_text) > 140:
        title_text = title_text[:140] + "…"

    return ResearchItem(
        source="discord",
        source_url=f"https://discord.com/channels/@me/{msg.get('channel_id')}/{msg.get('id')}",
        source_label=channel_label,
        title=title_text,
        body=body,
        author=author,
        score=None,
        posted_at=ts,
        game_slug=game_slug,
        tags=[channel_label],
    )


def _is_real_content(msg: dict) -> bool:
    """Filter out Discord system messages and emptyplaceholders.

    - type != 0 (default) is a system marker we don't want. In particular:
      * 12 = CHANNEL_FOLLOW_ADD ("X has followed #foo")
      * 18 = THREAD_CREATED, 21 = THREAD_STARTER_MESSAGE, etc.
    - A message with both empty content AND no embeds carries no info.
    """
    if msg.get("type") != 0:
        return False
    if (msg.get("content") or "").strip():
        return True
    if msg.get("embeds"):
        return True
    return False


def _fetch_via_bot(game: GameConfig) -> list[ResearchItem]:
    token = get_env().discord_bot_token
    if not token:
        _log.info("DISCORD_BOT_TOKEN not set — skipping bot path")
        return []
    items: list[ResearchItem] = []
    for mc in game.sources.discord.mirror_channels:
        try:
            messages = _fetch_channel_messages(mc.channel_id, token, mc.fetch_limit)
        except Exception as e:  # noqa: BLE001
            _log.warning("discord bot fetch failed for %s: %s", mc.label or mc.channel_id, e)
            continue
        kept = 0
        for m in messages:
            if not _is_real_content(m):
                continue
            items.append(_msg_to_item(m, game.slug, mc.label or mc.channel_id))
            kept += 1
        _log.info(
            "discord %s: %d total messages, %d real content",
            mc.label or mc.channel_id, len(messages), kept,
        )
    return items


# ---------- MANUAL-PASTE path --------------------------------------------- #


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


def _fetch_via_paste(game: GameConfig) -> list[ResearchItem]:
    ensure_dirs()
    path = inbox_path(game)
    if not path.exists():
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
                    tags=[current_channel] if current_channel else ["manual-paste"],
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
        if pending is not None and line.strip():
            pending["body"] = (pending["body"] + "\n" + line).strip()

    flush()
    return items


# ---------- public entry point -------------------------------------------- #


def fetch(game: GameConfig) -> list[ResearchItem]:
    items = _fetch_via_bot(game)
    items.extend(_fetch_via_paste(game))
    return items
