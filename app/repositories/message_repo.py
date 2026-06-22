"""Repository for :class:`app.db.models.message.ScheduledMessage`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.enums import MessageStatus
from app.db.models.message import ScheduledMessage


async def create(session: AsyncSession, **fields: Any) -> ScheduledMessage:
    """Create and flush a new scheduled message, returning the refreshed row."""
    message = ScheduledMessage(**fields)
    session.add(message)
    await session.flush()
    await session.refresh(message)
    return message


async def get(session: AsyncSession, mid: int) -> ScheduledMessage | None:
    """Return a scheduled message by primary key, or ``None``."""
    return await session.get(ScheduledMessage, mid)


async def set_job_id(
    session: AsyncSession, mid: int, job_id: str | None
) -> ScheduledMessage | None:
    """Store the APScheduler job id on the message row."""
    message = await session.get(ScheduledMessage, mid)
    if message is None:
        return None
    message.apscheduler_job_id = job_id
    await session.flush()
    return message


async def mark_sent(
    session: AsyncSession, mid: int
) -> ScheduledMessage | None:
    """Mark a scheduled message as sent, stamping ``sent_at``."""
    message = await session.get(ScheduledMessage, mid)
    if message is None:
        return None
    message.status = MessageStatus.sent
    message.sent_at = utcnow()
    await session.flush()
    return message


async def mark_failed(
    session: AsyncSession, mid: int
) -> ScheduledMessage | None:
    """Mark a scheduled message as failed."""
    message = await session.get(ScheduledMessage, mid)
    if message is None:
        return None
    message.status = MessageStatus.failed
    await session.flush()
    return message


async def mark_cancelled(
    session: AsyncSession, mid: int
) -> ScheduledMessage | None:
    """Mark a scheduled message as cancelled."""
    message = await session.get(ScheduledMessage, mid)
    if message is None:
        return None
    message.status = MessageStatus.cancelled
    await session.flush()
    return message


async def list_pending(session: AsyncSession) -> list[ScheduledMessage]:
    """Return all pending scheduled messages ordered by send time."""
    result = await session.execute(
        select(ScheduledMessage)
        .where(ScheduledMessage.status == MessageStatus.pending)
        .order_by(ScheduledMessage.send_at)
    )
    return list(result.scalars().all())
