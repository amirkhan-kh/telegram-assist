"""Inline-button callback tests — parse + Done/Snooze/Cancel apply correctly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.bot.handlers import _apply_callback
from app.bot.keyboards import (
    KIND_REMINDER,
    VERB_CANCEL,
    VERB_DONE,
    VERB_SNOOZE,
    Callback,
    parse_callback,
)
from app.db.models.enums import ReminderStatus
from app.repositories import person_repo, reminder_repo
from app.services._timeutil import as_utc


def test_parse_callback_valid_and_invalid():
    cb = parse_callback("snz:rem:42:tmrw")
    assert cb is not None
    assert (cb.verb, cb.kind, cb.item_id, cb.arg) == ("snz", "rem", 42, "tmrw")

    cb2 = parse_callback("done:prm:7")
    assert cb2 is not None and cb2.arg is None

    assert parse_callback("bad") is None
    assert parse_callback("done:rem:x") is None  # non-numeric id


async def _make_reminder(registry, *, when: datetime):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    return await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="suv ich", when_dt=when
    )


async def test_callback_done_marks_reminder_done(registry):
    rem = await _make_reminder(registry, when=datetime.now(UTC) + timedelta(hours=2))
    cb = Callback(VERB_DONE, KIND_REMINDER, rem.id)
    toast, status_line = await _apply_callback(registry, cb, datetime.now(UTC))

    assert "Bajarildi" in toast
    assert status_line is not None
    async with registry.session() as session:
        row = await reminder_repo.get(session, rem.id)
    assert row.status == ReminderStatus.done


async def test_callback_snooze_moves_due_and_keeps_pending(registry):
    rem = await _make_reminder(registry, when=datetime.now(UTC) + timedelta(hours=2))
    now = datetime.now(UTC)
    cb = Callback(VERB_SNOOZE, KIND_REMINDER, rem.id, "60")
    toast, _ = await _apply_callback(registry, cb, now)

    assert toast  # a confirmation toast was produced
    async with registry.session() as session:
        row = await reminder_repo.get(session, rem.id)
    assert row.status == ReminderStatus.pending
    # Due moved to ~now+60min (within a minute of the expected target).
    delta = abs((as_utc(row.due_at) - (now + timedelta(minutes=60))).total_seconds())
    assert delta < 60


async def test_callback_cancel_undoes_reminder(registry):
    rem = await _make_reminder(registry, when=datetime.now(UTC) + timedelta(hours=2))
    cb = Callback(VERB_CANCEL, KIND_REMINDER, rem.id)
    toast, _ = await _apply_callback(registry, cb, datetime.now(UTC))

    assert "Bekor" in toast
    async with registry.session() as session:
        row = await reminder_repo.get(session, rem.id)
    assert row.status == ReminderStatus.cancelled


async def test_confirm_button_acknowledges_morning_plan(registry):
    from app.bot.keyboards import KIND_BRIEFING, VERB_CONFIRM

    # The morning gate is disabled (plan is informational), but tapping an old
    # «✅ Tasdiqlash» button still gives a friendly acknowledgement and never
    # leaves a pending gate.
    cb = Callback(VERB_CONFIRM, KIND_BRIEFING, 0)
    toast, _ = await _apply_callback(registry, cb, datetime.now(UTC))

    assert "Tasdiqland" in toast
    assert await registry.briefing_service.is_morning_pending() is False
