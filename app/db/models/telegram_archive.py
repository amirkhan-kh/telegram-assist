"""Telegram archive index models.

These tables cache the owner's visible Telegram dialogs/messages so Jarvis/Joni
can answer archive-search requests from local DB first instead of scanning
Telegram history on every command.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class TelegramArchiveDialog(Base, TimestampMixin):
    __tablename__ = "telegram_archive_dialogs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dialog_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_indexed_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    oldest_indexed_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    history_fully_indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TelegramArchiveMessage(Base, TimestampMixin):
    __tablename__ = "telegram_archive_messages"
    __table_args__ = (
        UniqueConstraint("dialog_id", "message_id"),
        Index("ix_tg_archive_msg_kind_media_sent", "chat_kind", "media_kind", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dialog_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_title: Mapped[str] = mapped_column(String(512), index=True)
    chat_kind: Mapped[str] = mapped_column(String(32), index=True)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_label: Mapped[str] = mapped_column(String(512), default="Noma'lum")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_kind: Mapped[str] = mapped_column(String(32), default="text", index=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    out: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis_text: Mapped[str | None] = mapped_column(Text, nullable=True)
