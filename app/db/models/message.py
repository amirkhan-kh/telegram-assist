"""ScheduledMessage model — a deferred outbound message (text/voice via userbot).

Created by the ``schedule_message`` intent, e.g. "Amirhonga 1 soatda ... yubor".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import MessageStatus, SendMode, Source


class ScheduledMessage(Base, TimestampMixin):
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    recipient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id"), nullable=True
    )
    # resolved telegram peer (filled at send time if not known up front)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    delivery: Mapped[SendMode] = mapped_column(
        SAEnum(SendMode, native_enum=False, length=16), default=SendMode.voice
    )
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[MessageStatus] = mapped_column(
        SAEnum(MessageStatus, native_enum=False, length=16),
        default=MessageStatus.pending,
        index=True,
    )
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[Source] = mapped_column(
        SAEnum(Source, native_enum=False, length=16), default=Source.nlu
    )
