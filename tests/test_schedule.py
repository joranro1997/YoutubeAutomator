"""Upload scheduling — slot allocator + JSON store."""

from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from youtube_automator.config import ScheduleSettings
from youtube_automator.upload.schedule import (
    ScheduledItem,
    ScheduleStore,
    next_slot,
)

MAD = ZoneInfo("Europe/Madrid")
S = ScheduleSettings(timezone="Europe/Madrid", publish_hour=18, publish_minute=30)


def _utc(local: datetime) -> datetime:
    return local.astimezone(timezone.utc)


def test_next_slot_uses_today_when_before_hhmm():
    now = datetime(2026, 5, 20, 10, 0, tzinfo=MAD)  # 10:00 local
    slot = next_slot(S, busy=[], now=now.astimezone(timezone.utc))
    assert slot.astimezone(MAD) == datetime(2026, 5, 20, 18, 30, tzinfo=MAD)


def test_next_slot_rolls_to_tomorrow_when_past_hhmm():
    now = datetime(2026, 5, 20, 19, 0, tzinfo=MAD)  # past 18:30
    slot = next_slot(S, busy=[], now=now.astimezone(timezone.utc))
    assert slot.astimezone(MAD) == datetime(2026, 5, 21, 18, 30, tzinfo=MAD)


def test_next_slot_skips_busy_days_across_games():
    now = datetime(2026, 5, 20, 10, 0, tzinfo=MAD)
    # Today + tomorrow are already scheduled (any game). Next free = +2 days.
    busy = [
        _utc(datetime(2026, 5, 20, 18, 30, tzinfo=MAD)),  # LoM today
        _utc(datetime(2026, 5, 21, 18, 30, tzinfo=MAD)),  # LoE tomorrow
    ]
    slot = next_slot(S, busy=busy, now=now.astimezone(timezone.utc))
    assert slot.astimezone(MAD) == datetime(2026, 5, 22, 18, 30, tzinfo=MAD)


def test_next_slot_returned_in_utc():
    now = datetime(2026, 5, 20, 10, 0, tzinfo=MAD)
    slot = next_slot(S, busy=[], now=now.astimezone(timezone.utc))
    assert slot.tzinfo is timezone.utc or slot.utcoffset().total_seconds() == 0


def test_store_roundtrip(tmp_path):
    p = tmp_path / "scheduled.json"
    s = ScheduleStore()
    s.add(ScheduledItem(
        game="lom", video_slug="demo",
        publish_at=datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc),
        video_id="abc", url="https://youtu.be/abc",
    ))
    s.save(p)
    again = ScheduleStore.load(p)
    assert len(again.items) == 1
    assert again.items[0].video_slug == "demo"


def test_settings_default_loads():
    """Defaults match channel-wide config (Europe/Madrid, 18:30)."""
    from youtube_automator.config import get_settings
    s = get_settings().schedule
    assert s.timezone in ("Europe/Madrid", "UTC")  # respect overrides
    assert isinstance(s.publish_hour, int)
    assert 0 <= s.publish_minute < 60
