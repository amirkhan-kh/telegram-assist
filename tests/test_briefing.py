"""Briefing tests — morning plan + evening review build the right cards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.repositories import person_repo


class _CapturingNotifier:
    """Records (text, reply_markup) of every owner notification."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def notify_owner(self, text, *, reply_markup=None, parse_mode=None):
        self.calls.append((text, reply_markup))

    async def notify_owner_voice(self, text):
        self.calls.append((text, None))


async def _reminder_due_today(registry):
    """Create a pending reminder due LATER today (upcoming).

    Transient reminders drop out of the plan once their time passes, so the test
    fixture must be in the future — 2h ahead, capped at 23:30 to stay today.
    """
    tz = ZoneInfo(registry.settings.user_timezone)
    local_now = datetime.now(tz)
    target = local_now + timedelta(hours=2)
    end_of_day = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    if target > end_of_day:
        target = end_of_day
    due = target.astimezone(UTC)
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    return await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="Bugungi muhim ish", when_dt=due
    )


async def _scheduled_meeting(registry, *, days_ago: int, title: str):
    """Create a scheduled meeting whose start is ``days_ago`` days in the past."""
    from app.repositories import meeting_repo

    tz = ZoneInfo(registry.settings.user_timezone)
    start_local = (datetime.now(tz) - timedelta(days=days_ago)).replace(
        hour=20, minute=0, second=0, microsecond=0
    )
    start = start_local.astimezone(UTC)
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        await meeting_repo.create(
            session,
            owner_id=owner.id,
            title=title,
            start_at=start,
            end_at=start + timedelta(hours=1),
        )


async def test_morning_overdue_is_yesterday_only(registry):
    """'Kechagi bajarilmaganlar' shows yesterday's leftovers, not older ones."""
    notifier = _CapturingNotifier()
    registry.notification_service = notifier
    await _scheduled_meeting(registry, days_ago=1, title="Kechagi miting")
    await _scheduled_meeting(registry, days_ago=2, title="Eski miting")

    text = await registry.briefing_service.run_morning()
    assert text is not None
    # Yesterday's meeting surfaces (in overdue + priorities); the 2-day-old one
    # is dropped entirely — it must not appear anywhere in the plan.
    assert "Kechagi miting" in text
    assert "Eski miting" not in text


async def test_morning_briefing_lists_today(registry):
    notifier = _CapturingNotifier()
    registry.notification_service = notifier
    await _reminder_due_today(registry)

    text = await registry.briefing_service.run_morning()
    assert text is not None
    assert "Xayrli tong" in text
    assert "Bugungi muhim ish" in text
    assert "prioritet" in text.lower()
    # The post is delivered as a single message.
    assert len(notifier.calls) == 1


async def test_evening_review_sends_checklist(registry):
    notifier = _CapturingNotifier()
    registry.notification_service = notifier
    await _reminder_due_today(registry)

    text = await registry.briefing_service.run_evening()
    assert text is not None
    assert "Kun yakuni" in text
    assert "belgilang" in text.lower()
    # One interactive checklist message with a tap-to-complete keyboard.
    assert len(notifier.calls) == 1
    assert notifier.calls[0][1] is not None  # reply_markup (checklist) present


async def test_eod_done_removes_item_from_checklist(registry):
    """Tapping an item done removes it from the live end-of-day list."""
    registry.notification_service = _CapturingNotifier()
    rem = await _reminder_due_today(registry)
    now = datetime.now(UTC)

    items = await registry.briefing_service.collect_eod(now)
    assert any(kind == "rem" and rid == rem.id for kind, rid, _t in items)

    # Mark it done -> it leaves the end-of-day list.
    await registry.reminder_service.mark_done(rem.id)
    items_after = await registry.briefing_service.collect_eod(now)
    assert all(rid != rem.id for _k, rid, _t in items_after)


async def test_eod_move_to_tomorrow(registry):
    """Move-to-tomorrow reschedules a leftover reminder to a future time."""
    from app.repositories import reminder_repo
    from app.services._timeutil import as_utc, snooze_target

    registry.notification_service = _CapturingNotifier()
    rem = await _reminder_due_today(registry)
    now = datetime.now(UTC)
    target = snooze_target(now, "tmrw", registry.settings.user_timezone)

    await registry.reminder_service.snooze(rem.id, target)
    async with registry.session() as session:
        row = await reminder_repo.get(session, rem.id)
    assert as_utc(row.due_at) > now


async def test_morning_briefing_empty_day(registry):
    notifier = _CapturingNotifier()
    registry.notification_service = notifier
    text = await registry.briefing_service.run_morning()
    assert text is not None
    assert "Xayrli tong" in text
    # Nothing scheduled -> a friendly "empty day" line, not a crash.
    assert "bo'sh" in text.lower()


async def test_morning_plan_delivers_without_gate(registry):
    notifier = _CapturingNotifier()
    registry.notification_service = notifier
    bs = registry.briefing_service
    assert await bs.is_morning_pending() is False
    # The morning plan is informational: it is delivered with NO «✅ Tasdiqlash»
    # button and never raises the gate, so the owner can act immediately.
    await bs.run_morning()
    assert await bs.is_morning_pending() is False
    assert notifier.calls, "morning plan should be delivered"
    text, reply_markup = notifier.calls[-1]
    assert "Xayrli tong" in text
    assert reply_markup is None  # no confirmation button
