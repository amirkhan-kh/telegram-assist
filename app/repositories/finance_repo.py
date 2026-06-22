"""Repository for :class:`app.db.models.finance.DebtRecord`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.enums import DebtDirection, DebtStatus
from app.db.models.finance import DebtRecord


async def create(session: AsyncSession, **fields: Any) -> DebtRecord:
    """Create and flush a new debt record, returning the refreshed row."""
    record = DebtRecord(**fields)
    session.add(record)
    await session.flush()
    await session.refresh(record)
    return record


async def get(session: AsyncSession, did: int) -> DebtRecord | None:
    """Return a debt record by primary key, or ``None``."""
    return await session.get(DebtRecord, did)


async def list_open(
    session: AsyncSession, *, direction: DebtDirection | None = None
) -> list[DebtRecord]:
    """Return non-settled debt records, optionally filtered by direction."""
    stmt = select(DebtRecord).where(DebtRecord.status != DebtStatus.settled)
    if direction is not None:
        stmt = stmt.where(DebtRecord.direction == direction)
    stmt = stmt.order_by(DebtRecord.due_at.is_(None), DebtRecord.due_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete(session: AsyncSession, did: int) -> bool:
    """Hard-delete a debt record (used for instant undo). ``True`` if it existed."""
    record = await session.get(DebtRecord, did)
    if record is None:
        return False
    await session.delete(record)
    await session.flush()
    return True


async def settle(session: AsyncSession, did: int) -> DebtRecord | None:
    """Mark a debt record as settled, stamping ``settled_at``."""
    record = await session.get(DebtRecord, did)
    if record is None:
        return None
    record.status = DebtStatus.settled
    record.settled_at = utcnow()
    await session.flush()
    await session.refresh(record)
    return record


async def set_job_id(
    session: AsyncSession, did: int, job_id: str | None
) -> DebtRecord | None:
    """Store the debt reminder APScheduler job id on the record."""
    record = await session.get(DebtRecord, did)
    if record is None:
        return None
    record.reminder_job_id = job_id
    await session.flush()
    return record
