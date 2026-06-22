"""Repository for :class:`app.db.models.event.Event`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import EventStatus
from app.db.models.event import Event


async def create(session: AsyncSession, **fields: Any) -> Event:
    """Create and flush a new event, returning the refreshed row."""
    event = Event(**fields)
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return event


async def get(session: AsyncSession, eid: int) -> Event | None:
    """Return an event by primary key, or ``None``."""
    return await session.get(Event, eid)


async def set_status(
    session: AsyncSession, eid: int, status: EventStatus
) -> Event | None:
    """Update the event status."""
    event = await session.get(Event, eid)
    if event is None:
        return None
    event.status = status
    await session.flush()
    return event


async def list_active(session: AsyncSession, owner_id: int) -> list[Event]:
    """Return the owner's active events ordered by next occurrence."""
    result = await session.execute(
        select(Event)
        .where(Event.owner_id == owner_id, Event.status == EventStatus.active)
        .order_by(Event.next_fire_at.is_(None), Event.next_fire_at)
    )
    return list(result.scalars().all())


async def list_upcoming(
    session: AsyncSession, owner_id: int, *, before: datetime
) -> list[Event]:
    """Return active events whose next occurrence falls on/before ``before``."""
    result = await session.execute(
        select(Event)
        .where(
            Event.owner_id == owner_id,
            Event.status == EventStatus.active,
            Event.next_fire_at.is_not(None),
            Event.next_fire_at <= before,
        )
        .order_by(Event.next_fire_at)
    )
    return list(result.scalars().all())
