"""Meeting + MeetingAlert models (Google Meet/Calendar — schema ready now)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import MeetingAlertKind, MeetingStatus, NotifyTargetKind


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"))
    title: Mapped[str] = mapped_column(String(500))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    meet_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gcal_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notify_target_kind: Mapped[NotifyTargetKind | None] = mapped_column(
        SAEnum(NotifyTargetKind, native_enum=False, length=10), nullable=True
    )
    # people.id (as str) or a raw telegram chat id, depending on notify_target_kind
    notify_target_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[MeetingStatus] = mapped_column(
        SAEnum(MeetingStatus, native_enum=False, length=16),
        default=MeetingStatus.scheduled,
        index=True,
    )


class MeetingAlert(Base, TimestampMixin):
    __tablename__ = "meeting_alerts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    offset_minutes: Mapped[int] = mapped_column(Integer)  # 30, 15, 0
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[MeetingAlertKind] = mapped_column(
        SAEnum(MeetingAlertKind, native_enum=False, length=16),
        default=MeetingAlertKind.reminder,
    )
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fired: Mapped[bool] = mapped_column(Boolean, default=False)
