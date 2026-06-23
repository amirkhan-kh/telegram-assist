"""Per-item manual delete buttons in the list views (like the debts «✅»).

Reminders, agenda, important dates, decisions and calendar events each render a
«🗑 …» button per item; tapping it deletes that item and refreshes the list. The
callback payload is ``del:<kind>:<id>:<src>`` (or ``delcal:<event_id>`` for
Google Calendar). All offline — calendar uses a fake service.
"""

from __future__ import annotations

from datetime import timedelta

from app.brain.intent_router import RoutedIntent
from app.brain.intents import (
    ListAgenda,
    ListDecisions,
    ListImportantDates,
    ListReminders,
    ShowCalendar,
)
from app.db.base import utcnow
from app.db.models.enums import EventCategory
from app.integrations.google.calendar import CalEvent
from app.repositories import person_repo, reminder_repo
from app.services.dispatcher import (
    delete_calendar_event,
    delete_list_item,
    dispatch,
)


async def _owner_id(registry) -> int:
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        return owner.id


def _callbacks(markup) -> list[str]:
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_reminders_list_has_delete_buttons_and_delete_works(registry):
    oid = await _owner_id(registry)
    rem = await registry.reminder_service.create_reminder(
        owner_id=oid, title="Suv ich", when_dt=utcnow() + timedelta(hours=3)
    )
    res = await dispatch(
        registry, RoutedIntent("list_reminders", ListReminders(), {}), now=utcnow()
    )
    assert f"del:rem:{rem.id}:rl" in _callbacks(res.reply_markup)

    toast, relist = await delete_list_item(registry, "rem", rem.id, "rl", now=utcnow())
    assert "chirildi" in toast
    async with registry.session() as session:
        active = await reminder_repo.list_active(session, oid)
    assert all(r.id != rem.id for r in active)
    # The refreshed list no longer offers the deleted item's button.
    assert f"del:rem:{rem.id}:rl" not in _callbacks(relist.reply_markup)


async def test_important_dates_delete_button_and_delete(registry):
    oid = await _owner_id(registry)
    ev = await registry.event_service.add_event(
        owner_id=oid,
        title="Tug'ilgan kun",
        category=EventCategory.birthday,
        month=8,
        day=5,
        yearly=True,
        remind_days_before=[1],
    )
    res = await dispatch(
        registry,
        RoutedIntent("list_important_dates", ListImportantDates(), {}),
        now=utcnow(),
    )
    assert f"del:evt:{ev.id}:id" in _callbacks(res.reply_markup)
    toast, _ = await delete_list_item(registry, "evt", ev.id, "id", now=utcnow())
    assert "chirildi" in toast


async def test_decisions_delete_button_and_delete(registry):
    oid = await _owner_id(registry)
    dec = await registry.decision_service.add(
        owner_id=oid, text="Yangi loyihani boshlash", tag=None
    )
    res = await dispatch(
        registry, RoutedIntent("list_decisions", ListDecisions(), {}), now=utcnow()
    )
    assert f"del:dec:{dec.id}:dc" in _callbacks(res.reply_markup)
    toast, _ = await delete_list_item(registry, "dec", dec.id, "dc", now=utcnow())
    assert "chirildi" in toast


async def test_agenda_has_per_item_delete_buttons(registry):
    oid = await _owner_id(registry)
    rem = await registry.reminder_service.create_reminder(
        owner_id=oid, title="Reja elementi", when_dt=utcnow() + timedelta(hours=2)
    )
    res = await dispatch(
        registry, RoutedIntent("list_agenda", ListAgenda(scope="all"), {}), now=utcnow()
    )
    assert f"del:rem:{rem.id}:ag" in _callbacks(res.reply_markup)


# ── calendar (Google) per-item delete ────────────────────────────────────────
class _FakeCalendar:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def available(self) -> bool:
        return True

    async def list_events(self, *, start, end, **kw):
        return [
            CalEvent(
                summary="Ali tug'ilgan kun",
                start=start + timedelta(hours=2),
                end=None,
                all_day=True,
                location=None,
                link=None,
                id="abc123",
            )
        ]

    async def delete_event(self, event_id: str, **kw) -> bool:
        self.deleted.append(event_id)
        return True


async def test_calendar_view_has_delete_button_and_delete_calls_api(registry):
    cal = _FakeCalendar()
    registry.calendar_service = cal
    res = await dispatch(
        registry, RoutedIntent("show_calendar", ShowCalendar(scope="week"), {}), now=utcnow()
    )
    assert "delcal:abc123" in _callbacks(res.reply_markup)

    toast, relist = await delete_calendar_event(registry, "abc123", now=utcnow())
    assert "chirildi" in toast
    assert cal.deleted == ["abc123"]
    assert relist is not None
