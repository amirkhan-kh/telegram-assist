"""Meeting dispatch tests — the no-Google path still creates alerts (1d/1h/0)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ScheduleMeeting, TimeSpec
from app.db.models.meeting import MeetingAlert
from app.repositories import meeting_repo, person_repo
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


async def test_schedule_meeting_creates_meeting_and_alerts(registry):
    routed = RoutedIntent(
        "schedule_meeting",
        ScheduleMeeting(
            title="Jamoa yig'ini",
            when=TimeSpec(raw="ertaga soat 10"),
            duration_minutes=30,
            invitee_names=[],
            create_meet_link=True,
            notify_target_name=None,
        ),
        {},
    )
    result = await dispatch(registry, routed, now=_now())

    assert "Uchrashuv rejalashtirildi" in result.text
    assert "Jamoa yig'ini" in result.text
    # No Google credentials in tests -> a clear note, not a crash.
    assert "Google ulanmagani" in result.text
    assert "1 kun va 1 soat" in result.text

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        meetings = await meeting_repo.list_upcoming(session, owner.id)
        assert len(meetings) == 1
        alerts = (
            (await session.execute(select(MeetingAlert))).scalars().all()
        )
    # 1-day, 1-hour reminders + the at-start link delivery = 3 alert rows.
    offsets = sorted(a.offset_minutes for a in alerts)
    assert offsets == [0, 60, 1440]


class _CapturingNotifier:
    """Records owner notifications so a test can assert what was sent."""

    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def notify_owner(self, text: str, **_kwargs: object) -> None:
        self.msgs.append(text)

    async def notify_owner_voice(self, text: str) -> None:
        self.msgs.append(text)


async def test_meeting_1h_alert_also_reminds_contact(registry):
    from app.db.models.enums import NotifyTargetKind

    notifier = _CapturingNotifier()
    registry.notification_service = notifier

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        bexruz = await person_repo.create(
            session, display_name="Bexruz", telegram_user_id=999001
        )
        owner_id, bexruz_id = owner.id, bexruz.id

    meeting = await registry.meeting_service.create_meeting(
        owner_id=owner_id,
        title="Sinov uchrashuvi",
        start_at=datetime(2027, 1, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2027, 1, 1, 10, 30, tzinfo=UTC),
        notify_target_kind=NotifyTargetKind.person,
        notify_target_ref=str(bexruz_id),
    )

    async with registry.session() as session:
        alerts = (
            (
                await session.execute(
                    select(MeetingAlert).where(MeetingAlert.meeting_id == meeting.id)
                )
            )
            .scalars()
            .all()
        )
    alert60 = next(a for a in alerts if a.offset_minutes == 60)

    notifier.msgs.clear()
    await registry.meeting_service.fire_alert(alert60.id, "offset:60")
    joined = "\n".join(notifier.msgs)
    # Owner notice + a written reminder for the contact (test-mode redirect).
    assert "Uchrashuv 1 soatdan keyin" in joined
    assert "[TEST -> Bexruz]" in joined
    assert "Sinov uchrashuvi" in joined


async def test_meeting_start_sends_started_link_to_contact(registry):
    from app.db.models.enums import NotifyTargetKind

    notifier = _CapturingNotifier()
    registry.notification_service = notifier

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        bexruz = await person_repo.create(
            session, display_name="Bexruz", telegram_user_id=999002
        )
        owner_id, bexruz_id = owner.id, bexruz.id

    meeting = await registry.meeting_service.create_meeting(
        owner_id=owner_id,
        title="Ish uchrashuvi",
        start_at=datetime(2027, 1, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2027, 1, 1, 10, 30, tzinfo=UTC),
        meet_link="https://meet.google.com/xyz-abcd-efg",
        notify_target_kind=NotifyTargetKind.person,
        notify_target_ref=str(bexruz_id),
    )

    async with registry.session() as session:
        alerts = (
            (
                await session.execute(
                    select(MeetingAlert).where(MeetingAlert.meeting_id == meeting.id)
                )
            )
            .scalars()
            .all()
        )
    link_alert = next(a for a in alerts if a.offset_minutes == 0)

    notifier.msgs.clear()
    await registry.meeting_service.deliver_link(link_alert.id)
    joined = "\n".join(notifier.msgs)
    # The contact is told the meeting started + given the Meet link (test redirect).
    assert "boshlandi" in joined
    assert "meet.google.com/xyz-abcd-efg" in joined
    assert "[TEST -> Bexruz]" in joined
