"""Repository for :class:`app.db.models.reminder.Reminder`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import ReminderStatus
from app.db.models.reminder import Reminder


async def create(session: AsyncSession, **fields: Any) -> Reminder:
    """Create and flush a new reminder, returning the refreshed row."""
    reminder = Reminder(**fields)
    session.add(reminder)
    await session.flush()
    await session.refresh(reminder)
    return reminder


async def get(session: AsyncSession, rid: int) -> Reminder | None:
    """Return a reminder by primary key, or ``None``."""
    return await session.get(Reminder, rid)


async def set_job_id(
    session: AsyncSession, rid: int, job_id: str | None
) -> Reminder | None:
    """Store the APScheduler job id on the reminder row."""
    reminder = await session.get(Reminder, rid)
    if reminder is None:
        return None
    reminder.apscheduler_job_id = job_id
    await session.flush()
    return reminder


async def set_status(
    session: AsyncSession, rid: int, status: ReminderStatus
) -> Reminder | None:
    """Update the reminder status."""
    reminder = await session.get(Reminder, rid)
    if reminder is None:
        return None
    reminder.status = status
    await session.flush()
    return reminder


async def list_active(session: AsyncSession, owner_id: int) -> list[Reminder]:
    """Return the owner's pending reminders ordered by due time."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.owner_id == owner_id,
            Reminder.status == ReminderStatus.pending,
        )
        .order_by(Reminder.due_at)
    )
    return list(result.scalars().all())


async def count_done_since(
    session: AsyncSession, owner_id: int, since: datetime
) -> int:
    """Count the owner's reminders marked done at/after ``since`` (today's wins)."""
    result = await session.execute(
        select(func.count())
        .select_from(Reminder)
        .where(
            Reminder.owner_id == owner_id,
            Reminder.status == ReminderStatus.done,
            Reminder.updated_at >= since,
        )
    )
    return int(result.scalar() or 0)
