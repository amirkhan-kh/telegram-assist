"""cancel_item tests — reminders, scheduled messages and meetings really cancel."""

from __future__ import annotations

from datetime import UTC, datetime

from app.brain.intent_router import RoutedIntent
from app.brain.intents import (
    CancelItem,
    CreateReminder,
    ScheduleMeeting,
    TimeSpec,
)
from app.db.models.enums import MeetingStatus, MessageStatus, ReminderStatus
from app.repositories import meeting_repo, message_repo, person_repo, reminder_repo
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


async def test_cancel_scheduled_message(registry):
    msg = await registry.message_service.schedule_message(
        recipient_id=None,
        chat_id=999,
        content="salom",
        send_at=datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
    )
    routed = RoutedIntent("cancel_item", CancelItem(item_kind="message", selector=str(msg.id)), {})
    result = await dispatch(registry, routed, now=_now())

    assert "bekor qilindi" in result.text.lower()
    async with registry.session() as session:
        row = await message_repo.get(session, msg.id)
    assert row.status == MessageStatus.cancelled


async def test_cancel_meeting(registry):
    await dispatch(
        registry,
        RoutedIntent(
            "schedule_meeting",
            ScheduleMeeting(
                title="Sinov",
                when=TimeSpec(raw="ertaga soat 10"),
                duration_minutes=30,
                invitee_names=[],
                create_meet_link=False,
                notify_target_name=None,
            ),
            {},
        ),
        now=_now(),
    )
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        meetings = await meeting_repo.list_upcoming(session, owner.id)
    mid = meetings[0].id

    result = await dispatch(
        registry,
        RoutedIntent("cancel_item", CancelItem(item_kind="meeting", selector=str(mid)), {}),
        now=_now(),
    )
    assert "bekor qilindi" in result.text.lower()
    async with registry.session() as session:
        meeting = await meeting_repo.get(session, mid)
    assert meeting.status == MeetingStatus.cancelled


async def test_cancel_reminder(registry):
    await dispatch(
        registry,
        RoutedIntent(
            "create_reminder",
            CreateReminder(
                text="suv ich",
                when=TimeSpec(raw="ertaga soat 10"),
                pre_alerts_minutes=[],
            ),
            {},
        ),
        now=_now(),
    )
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        reminders = await reminder_repo.list_active(session, owner.id)
    rid = reminders[0].id

    result = await dispatch(
        registry,
        RoutedIntent("cancel_item", CancelItem(item_kind="reminder", selector=str(rid)), {}),
        now=_now(),
    )
    assert "bekor qilindi" in result.text.lower()
    async with registry.session() as session:
        reminder = await reminder_repo.get(session, rid)
    assert reminder.status == ReminderStatus.cancelled


async def test_cancel_missing_message_is_graceful(registry):
    routed = RoutedIntent("cancel_item", CancelItem(item_kind="message", selector="9999"), {})
    result = await dispatch(registry, routed, now=_now())
    assert "topilmadi" in result.text.lower()
