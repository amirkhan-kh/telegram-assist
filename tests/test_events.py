"""Important-date (Event) tests — next-occurrence math, listing, dispatch."""

from __future__ import annotations

from app.brain.intent_router import RoutedIntent
from app.brain.intents import AddImportantDate, ListImportantDates
from app.db.base import utcnow
from app.db.models.enums import EventCategory, EventStatus
from app.repositories import person_repo
from app.services._timeutil import as_utc
from app.services.dispatcher import dispatch
from app.services.event_service import category_icon


def test_category_icon():
    assert category_icon(EventCategory.birthday) == "🎂"
    assert category_icon("payment") == "💳"
    assert category_icon("nonsense") == "📌"


async def test_add_event_computes_future_occurrence(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_event(
        owner_id=owner.id,
        title="Ali tug'ilgan kuni",
        category=EventCategory.birthday,
        month=1,
        day=1,
        yearly=True,
        remind_days_before=[7, 1],
    )
    assert event.status == EventStatus.active
    assert event.event_date.month == 1 and event.event_date.day == 1
    # The next occurrence is always in the future.
    assert event.next_fire_at is not None
    assert as_utc(event.next_fire_at) > utcnow()
    # 0 (the day) is always added to the pre-alert offsets.
    assert event.remind_days_before == [1, 7]


async def test_add_important_date_dispatch_and_list(registry):
    routed = RoutedIntent(
        "add_important_date",
        AddImportantDate(
            title="Pasport muddati",
            category="document",
            month=12,
            day=12,
            yearly=True,
            remind_days_before=[7],
        ),
        {},
    )
    result = await dispatch(registry, routed, now=utcnow())
    assert "Muhim sana saqlandi" in result.text
    assert "Pasport muddati" in result.text

    listed = await dispatch(
        registry, RoutedIntent("list_important_dates", ListImportantDates(), {}),
        now=utcnow(),
    )
    assert "Pasport muddati" in listed.text


async def test_event_cancel(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_event(
        owner_id=owner.id,
        title="Sug'urta",
        category=EventCategory.document,
        month=3,
        day=3,
    )
    ok = await registry.event_service.cancel(event.id)
    assert ok is True
    remaining = await registry.event_service.list_active(owner.id)
    assert all(e.id != event.id for e in remaining)
