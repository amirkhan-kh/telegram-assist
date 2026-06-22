"""Event model — important dates & birthdays (recurring yearly by default).

Covers the "muhim sanalar" the owner wants surfaced ahead of time: birthdays,
passport/insurance expiry, payment dates, travel, doctor visits. Each event
stores its month/day (the ``event_date``), whether it repeats yearly, and how
many days before to pre-alert. :class:`app.services.event_service.EventService`
schedules the alerts and, for yearly events, re-arms them for next year.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import EventCategory, EventStatus


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[EventCategory] = mapped_column(
        SAEnum(EventCategory, native_enum=False, length=16),
        default=EventCategory.other,
    )
    # The calendar date of the (next) occurrence; for yearly events the year is
    # rolled forward each time it fires.
    event_date: Mapped[date] = mapped_column(Date, index=True)
    yearly: Mapped[bool] = mapped_column(Boolean, default=True)
    # Days-before to pre-alert, e.g. [7, 1]; the day itself (0) is always added.
    remind_days_before: Mapped[list] = mapped_column(JSON, default=list)
    # The next concrete fire instant (event_date at the local alert hour, in UTC);
    # used to list upcoming events without re-deriving the schedule.
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[EventStatus] = mapped_column(
        SAEnum(EventStatus, native_enum=False, length=16),
        default=EventStatus.active,
        index=True,
    )
    # APScheduler job ids for this occurrence's alerts (cancelled on re-arm).
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
