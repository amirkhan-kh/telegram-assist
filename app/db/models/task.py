"""Task model — covers the user's own promises and delegated/dependent tasks.

`kind` discriminates:
  * self_promise — the OWNER promised to do something by ``due_at``
  * delegated    — someone (``owner_id`` = the assignee) owes the OWNER a
                   deliverable by ``due_at``; drives auto follow-ups
  * generic      — a plain to-do
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import Priority, SendMode, Source, TaskKind, TaskStatus


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    # responsible party: the owner for self_promise; the assignee for delegated
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"), index=True)
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"))
    # the other party (whom a promise is to / who delegated the task)
    counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[TaskKind] = mapped_column(
        SAEnum(TaskKind, native_enum=False, length=20), default=TaskKind.generic, index=True
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False, length=16),
        default=TaskStatus.open,
        index=True,
    )
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, native_enum=False, length=10), default=Priority.normal
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # outbound message body for follow-ups (the nudge sent to the assignee)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery: Mapped[SendMode] = mapped_column(
        SAEnum(SendMode, native_enum=False, length=16), default=SendMode.voice
    )
    parent_task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tasks.id"), nullable=True
    )
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[Source] = mapped_column(
        SAEnum(Source, native_enum=False, length=16), default=Source.manual
    )
