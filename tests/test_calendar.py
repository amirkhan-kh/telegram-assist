"""Pure free/busy gap-math tests for GoogleCalendarService (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.integrations.google.calendar import GoogleCalendarService

TZ = "Asia/Tashkent"
_TZINFO = ZoneInfo(TZ)


def _local(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TZINFO).astimezone(UTC)


def _svc() -> GoogleCalendarService:
    return GoogleCalendarService(None, timezone=TZ, work_start_hour=9, work_end_hour=18)


def test_working_window_is_one_day_block():
    svc = _svc()
    windows = svc._working_windows(_local(2026, 6, 19, 0), _local(2026, 6, 20, 0))
    assert len(windows) == 1
    w_start, w_end = windows[0]
    assert w_start == _local(2026, 6, 19, 9)
    assert w_end == _local(2026, 6, 19, 18)


def test_subtract_busy_splits_window():
    svc = _svc()
    windows = [(_local(2026, 6, 19, 9), _local(2026, 6, 19, 18))]
    busy = [(_local(2026, 6, 19, 11), _local(2026, 6, 19, 12))]
    free = svc._subtract_busy(windows, busy)
    assert free == [
        (_local(2026, 6, 19, 9), _local(2026, 6, 19, 11)),
        (_local(2026, 6, 19, 12), _local(2026, 6, 19, 18)),
    ]


def test_overlapping_busy_intervals_merge():
    svc = _svc()
    windows = [(_local(2026, 6, 19, 9), _local(2026, 6, 19, 18))]
    busy = [
        (_local(2026, 6, 19, 10), _local(2026, 6, 19, 12)),
        (_local(2026, 6, 19, 11), _local(2026, 6, 19, 13)),  # overlaps the first
    ]
    free = svc._subtract_busy(windows, busy)
    assert free == [
        (_local(2026, 6, 19, 9), _local(2026, 6, 19, 10)),
        (_local(2026, 6, 19, 13), _local(2026, 6, 19, 18)),
    ]


async def test_find_free_slots_filters_by_duration(monkeypatch):
    svc = _svc()

    async def fake_busy(*, start, end, calendar_id="primary"):
        # Leave only 09:00–09:20 and 17:00–18:00 free (one short, one long).
        return [
            (_local(2026, 6, 19, 9, 20), _local(2026, 6, 19, 17)),
        ]

    monkeypatch.setattr(svc, "get_busy", fake_busy)
    slots = await svc.find_free_slots(
        start=_local(2026, 6, 19, 0),
        end=_local(2026, 6, 20, 0),
        duration_minutes=30,
    )
    # The 20-minute gap is dropped; the 60-minute one survives.
    assert (_local(2026, 6, 19, 17), _local(2026, 6, 19, 18)) in slots
    assert all((e - s) >= timedelta(minutes=30) for s, e in slots)
