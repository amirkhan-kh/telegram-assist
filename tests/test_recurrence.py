"""Recurring-reminder tests — recurrence→cron mapping and recurring fire."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.brain.intents import RecurrenceSpec
from app.db.models.enums import ReminderStatus
from app.repositories import person_repo, reminder_repo
from app.services.dispatcher import _recurrence_to_cron


def test_recurrence_none_is_one_shot():
    fields, label = _recurrence_to_cron(None)
    assert fields is None and label == ""
    fields, _ = _recurrence_to_cron(RecurrenceSpec(freq="none"))
    assert fields is None


def test_recurrence_daily():
    fields, label = _recurrence_to_cron(RecurrenceSpec(freq="daily", hour=7, minute=30))
    assert fields == {"hour": 7, "minute": 30}
    assert "Har kuni" in label


def test_recurrence_weekly_maps_weekday_name():
    fields, label = _recurrence_to_cron(
        RecurrenceSpec(freq="weekly", weekday=0, hour=8, minute=0)
    )
    assert fields == {"day_of_week": "mon", "hour": 8, "minute": 0}
    assert "dushanba" in label.lower()


def test_recurrence_monthly_end():
    fields, label = _recurrence_to_cron(
        RecurrenceSpec(freq="monthly", month_end=True, hour=9, minute=0)
    )
    assert fields == {"day": "last", "hour": 9, "minute": 0}
    assert "oxir" in label.lower()


def test_recurrence_monthly_day():
    fields, _ = _recurrence_to_cron(
        RecurrenceSpec(freq="monthly", day_of_month=15, hour=10, minute=0)
    )
    assert fields == {"day": 15, "hour": 10, "minute": 0}


async def test_recurring_fire_keeps_reminder_pending(registry):
    """A recurring occurrence notifies but must NOT close the reminder."""
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    rem = await registry.reminder_service.create_reminder(
        owner_id=owner.id,
        title="kuni bilan suv ich",
        when_dt=datetime.now(UTC) + timedelta(days=1),
        recurrence="Har kuni 09:00",
        cron_fields={"hour": 9, "minute": 0},
    )
    async with registry.session() as session:
        created = await reminder_repo.get(session, rem.id)
    assert created.recurrence == "Har kuni 09:00"

    await registry.reminder_service.fire(rem.id, "recurring")
    async with registry.session() as session:
        row = await reminder_repo.get(session, rem.id)
    # Still pending so the cron keeps repeating.
    assert row.status == ReminderStatus.pending
