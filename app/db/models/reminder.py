"""Reminder model — generic one-shot / recurring reminders (daily plan)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import ReminderStatus, Source


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # cron expression or RRULE for recurring reminders; null => one-shot
    recurrence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ReminderStatus] = mapped_column(
        SAEnum(ReminderStatus, native_enum=False, length=16),
        default=ReminderStatus.pending,
        index=True,
    )
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[Source] = mapped_column(
        SAEnum(Source, native_enum=False, length=16), default=Source.manual
    )
