"""Google Calendar view tests — the show_calendar dispatch path (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ShowCalendar
from app.integrations.google.calendar import CalEvent
from app.services.dispatcher import dispatch


class _FakeCalendar:
    def __init__(self, events: list[CalEvent]) -> None:
        self._events = events

    def available(self) -> bool:
        return True

    async def list_events(self, *, start, end, **_kwargs) -> list[CalEvent]:
        return [e for e in self._events if start <= e.start < end]


async def test_show_calendar_renders_events(registry):
    now = datetime.now(UTC)
    registry.calendar_service = _FakeCalendar(
        [
            CalEvent(
                summary="Investor uchrashuvi",
                start=now + timedelta(hours=3),
                end=now + timedelta(hours=4),
                all_day=False,
                location=None,
                link="https://meet.google.com/abc",
            )
        ]
    )
    result = await dispatch(
        registry, RoutedIntent("show_calendar", ShowCalendar(scope="week"), {}), now=now
    )
    assert "Kalendar" in result.text
    assert "Investor uchrashuvi" in result.text
    assert result.parse_mode == "HTML"


async def test_show_calendar_not_connected(registry):
    registry.calendar_service = None
    result = await dispatch(
        registry, RoutedIntent("show_calendar", ShowCalendar(), {}), now=datetime.now(UTC)
    )
    assert "ulanmagan" in result.text


async def test_show_calendar_empty(registry):
    registry.calendar_service = _FakeCalendar([])
    result = await dispatch(
        registry,
        RoutedIntent("show_calendar", ShowCalendar(scope="today"), {}),
        now=datetime.now(UTC),
    )
    assert "bo'sh" in result.text
