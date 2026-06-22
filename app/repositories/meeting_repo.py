"""Repository for :class:`app.db.models.meeting.Meeting` and ``MeetingAlert``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import MeetingStatus
from app.db.models.meeting import Meeting, MeetingAlert


async def create(session: AsyncSession, **fields: Any) -> Meeting:
    """Create and flush a new meeting, returning the refreshed row."""
    meeting = Meeting(**fields)
    session.add(meeting)
    await session.flush()
    await session.refresh(meeting)
    return meeting


async def get(session: AsyncSession, mid: int) -> Meeting | None:
    """Return a meeting by primary key, or ``None``."""
    return await session.get(Meeting, mid)


async def add_alert(session: AsyncSession, **fields: Any) -> MeetingAlert:
    """Create and flush a new meeting alert, returning the refreshed row."""
    alert = MeetingAlert(**fields)
    session.add(alert)
    await session.flush()
    await session.refresh(alert)
    return alert


async def get_alert(session: AsyncSession, aid: int) -> MeetingAlert | None:
    """Return a meeting alert by primary key, or ``None``."""
    return await session.get(MeetingAlert, aid)


async def set_alert_job_id(
    session: AsyncSession, aid: int, job_id: str | None
) -> MeetingAlert | None:
    """Store the APScheduler job id on the alert row."""
    alert = await session.get(MeetingAlert, aid)
    if alert is None:
        return None
    alert.apscheduler_job_id = job_id
    await session.flush()
    return alert


async def mark_alert_fired(
    session: AsyncSession, aid: int
) -> MeetingAlert | None:
    """Mark a meeting alert as fired."""
    alert = await session.get(MeetingAlert, aid)
    if alert is None:
        return None
    alert.fired = True
    await session.flush()
    return alert


async def reset_alert_fired(
    session: AsyncSession, aid: int
) -> MeetingAlert | None:
    """Re-arm a meeting alert (mark it not-yet-fired) after a meeting is moved."""
    alert = await session.get(MeetingAlert, aid)
    if alert is None:
        return None
    alert.fired = False
    await session.flush()
    return alert


async def set_status(
    session: AsyncSession, mid: int, status: MeetingStatus
) -> Meeting | None:
    """Update a meeting's status."""
    meeting = await session.get(Meeting, mid)
    if meeting is None:
        return None
    meeting.status = status
    await session.flush()
    return meeting


async def list_alerts(
    session: AsyncSession, meeting_id: int
) -> list[MeetingAlert]:
    """Return all alert rows for a meeting (for cancellation/inspection)."""
    result = await session.execute(
        select(MeetingAlert).where(MeetingAlert.meeting_id == meeting_id)
    )
    return list(result.scalars().all())


async def list_upcoming(session: AsyncSession, owner_id: int) -> list[Meeting]:
    """Return the owner's scheduled meetings ordered by start time."""
    result = await session.execute(
        select(Meeting)
        .where(
            Meeting.owner_id == owner_id,
            Meeting.status == MeetingStatus.scheduled,
        )
        .order_by(Meeting.start_at)
    )
    return list(result.scalars().all())
