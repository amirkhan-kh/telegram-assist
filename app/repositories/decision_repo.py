"""Repository for :class:`app.db.models.decision.Decision`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.decision import Decision


async def create(session: AsyncSession, **fields: Any) -> Decision:
    """Create and flush a new decision, returning the refreshed row."""
    decision = Decision(**fields)
    session.add(decision)
    await session.flush()
    await session.refresh(decision)
    return decision


async def get(session: AsyncSession, did: int) -> Decision | None:
    """Return a decision by primary key, or ``None``."""
    return await session.get(Decision, did)


async def delete(session: AsyncSession, did: int) -> bool:
    """Hard-delete a decision (instant undo). ``True`` if it existed."""
    decision = await session.get(Decision, did)
    if decision is None:
        return False
    await session.delete(decision)
    await session.flush()
    return True


async def list_recent(
    session: AsyncSession, owner_id: int, *, limit: int = 20
) -> list[Decision]:
    """Return the owner's most recent decisions (newest first)."""
    result = await session.execute(
        select(Decision)
        .where(Decision.owner_id == owner_id)
        .order_by(Decision.decided_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
