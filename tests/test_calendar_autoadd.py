"""Tests: date-bearing commands mirror onto Google Calendar (best-effort).

Important dates, one-shot reminders and promises should also appear under the
Calendar view. The sync is best-effort: when no calendar is wired it is silently
skipped and the item is still saved internally. A fake calendar records the
create_event calls so we can assert the right shape (all-day + yearly RRULE for
dates, timed for reminders/promises).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.brain.intent_router import RoutedIntent
from app.brain.intents import (
    AddImportantDate,
    CreatePromise,
    CreateReminder,
    TimeSpec,
)
from app.db.base import utcnow
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 23, 8, 0, tzinfo=UTC)


class _FakeCalendar:
    """Records create_event calls; reports itself as connected."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    def available(self) -> bool:
        return True

    async def create_event(self, **kwargs) -> dict:
        self.created.append(kwargs)
        return {"htmlLink": "https://calendar.google.com/event/xyz"}


async def test_important_date_creates_all_day_yearly_event(registry):
    cal = _FakeCalendar()
    registry.calendar_service = cal
    routed = RoutedIntent(
        "add_important_date",
        AddImportantDate(
            title="Ali tug'ilgan kuni",
            category="birthday",
            month=8,
            day=5,
            yearly=True,
            remind_days_before=[1],
        ),
        {},
    )
    await dispatch(registry, routed, now=utcnow())
    assert len(cal.created) == 1
    ev = cal.created[0]
    assert ev["title"] == "Ali tug'ilgan kuni"
    assert ev["all_day"] is True
    assert ev["recurrence"] == ["RRULE:FREQ=YEARLY"]


async def test_important_date_one_off_has_no_recurrence(registry):
    cal = _FakeCalendar()
    registry.calendar_service = cal
    routed = RoutedIntent(
        "add_important_date",
        AddImportantDate(
            title="Konferensiya",
            category="other",
            month=9,
            day=1,
            year=2026,
            yearly=False,
            remind_days_before=[1],
        ),
        {},
    )
    await dispatch(registry, routed, now=utcnow())
    assert len(cal.created) == 1
    assert cal.created[0]["recurrence"] is None
    assert cal.created[0]["all_day"] is True


async def test_one_shot_reminder_creates_timed_event_and_notes_it(registry):
    cal = _FakeCalendar()
    registry.calendar_service = cal
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="Hujjat topshirish", when=TimeSpec(raw="ertaga soat 10")),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Kalendarga qo'shildi" in result.text
    assert len(cal.created) == 1
    assert cal.created[0]["all_day"] is False
    assert cal.created[0]["title"] == "Hujjat topshirish"


async def test_promise_creates_timed_calendar_event(registry):
    cal = _FakeCalendar()
    registry.calendar_service = cal
    routed = RoutedIntent(
        "create_promise",
        CreatePromise(what="Hisobotni yakunlash", deadline=TimeSpec(raw="ertaga soat 15")),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Kalendarga qo'shildi" in result.text
    assert len(cal.created) == 1
    assert cal.created[0]["all_day"] is False


async def test_reminder_without_calendar_is_graceful(registry):
    registry.calendar_service = None
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="Suv ich", when=TimeSpec(raw="ertaga soat 9")),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Eslatma qo'yildi" in result.text
    assert "Kalendarga" not in result.text
