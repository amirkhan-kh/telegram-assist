"""Repository for :class:`app.db.models.task.Task`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import TaskKind, TaskStatus
from app.db.models.task import Task


async def create(session: AsyncSession, **fields: Any) -> Task:
    """Create and flush a new task, returning the refreshed row."""
    task = Task(**fields)
    session.add(task)
    await session.flush()
    await session.refresh(task)
    return task


async def get(session: AsyncSession, tid: int) -> Task | None:
    """Return a task by primary key, or ``None``."""
    return await session.get(Task, tid)


async def set_job_id(
    session: AsyncSession, tid: int, job_id: str | None
) -> Task | None:
    """Store the APScheduler job id on the task row."""
    task = await session.get(Task, tid)
    if task is None:
        return None
    task.apscheduler_job_id = job_id
    await session.flush()
    return task


async def set_status(
    session: AsyncSession, tid: int, status: TaskStatus
) -> Task | None:
    """Update the task status."""
    task = await session.get(Task, tid)
    if task is None:
        return None
    task.status = status
    await session.flush()
    return task


async def list_open(
    session: AsyncSession,
    *,
    owner_id: int | None = None,
    kind: TaskKind | None = None,
) -> list[Task]:
    """Return open / in-progress tasks, optionally filtered by owner and kind."""
    stmt = select(Task).where(
        Task.status.in_((TaskStatus.open, TaskStatus.in_progress))
    )
    if owner_id is not None:
        stmt = stmt.where(Task.owner_id == owner_id)
    if kind is not None:
        stmt = stmt.where(Task.kind == kind)
    stmt = stmt.order_by(Task.due_at.is_(None), Task.due_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_done_since(
    session: AsyncSession,
    owner_id: int,
    since: datetime,
    *,
    kind: TaskKind | None = None,
) -> int:
    """Count tasks for ``owner_id`` marked done at/after ``since``."""
    stmt = (
        select(func.count())
        .select_from(Task)
        .where(
            Task.owner_id == owner_id,
            Task.status == TaskStatus.done,
            Task.updated_at >= since,
        )
    )
    if kind is not None:
        stmt = stmt.where(Task.kind == kind)
    result = await session.execute(stmt)
    return int(result.scalar() or 0)
