"""Upload scheduling — one video/day at the configured local time across
BOTH games. Pure timezone math + a small JSON cache of scheduled uploads.

When `yta watch-and-upload` is ready to ship a new MP4, it asks
`next_slot()` for a free publishAt (UTC) and uploads with
privacy=private + publishAt set, so YouTube auto-publishes at that
moment. The local cache prevents two consecutive uploads landing on the
same day, even when YouTube's quota lets us push them back-to-back.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 unsupported
    raise

from ..config import ScheduleSettings
from ..paths import OUTPUTS_DIR

SCHEDULE_FILE = OUTPUTS_DIR / "scheduled.json"


class ScheduledItem(BaseModel):
    """One scheduled (or published) upload, persisted across runs."""

    game: str
    video_slug: str
    publish_at: datetime          # UTC
    video_id: str = ""            # YouTube video id
    url: str = ""


class ScheduleStore(BaseModel):
    items: list[ScheduledItem] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path = SCHEDULE_FILE) -> "ScheduleStore":
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path = SCHEDULE_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    def add(self, item: ScheduledItem) -> None:
        self.items.append(item)


def next_slot(
    settings: ScheduleSettings,
    busy: list[datetime],
    *,
    now: datetime | None = None,
) -> datetime:
    """Return the next free publishAt (UTC) for an upload.

    `busy` = the publish_at datetimes already taken (any game). Returns
    HH:MM local on the first day in (today, today+30] that no slot occupies.
    """
    tz = ZoneInfo(settings.timezone)
    now = now or datetime.now(timezone.utc)
    today_local = now.astimezone(tz).date()
    slot_today = datetime.combine(
        today_local, time(settings.publish_hour, settings.publish_minute), tzinfo=tz
    )
    start_day = today_local if now.astimezone(tz) < slot_today else today_local + timedelta(days=1)

    busy_days: set[date] = {dt.astimezone(tz).date() for dt in busy}
    day = start_day
    while day in busy_days:
        day += timedelta(days=1)
    local_dt = datetime.combine(
        day, time(settings.publish_hour, settings.publish_minute), tzinfo=tz
    )
    return local_dt.astimezone(timezone.utc)
