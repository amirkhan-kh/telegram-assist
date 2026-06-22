"""Declarative base + shared mixins.

All datetimes are stored timezone-aware in UTC; conversion to the user's local
zone (Asia/Tashkent) happens only at display / scheduling-input boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 64-bit primary-key type that autoincrements on *both* backends.
#
# SQLite only treats ``INTEGER PRIMARY KEY`` as an auto-incrementing alias for
# the implicit rowid; a ``BIGINT PRIMARY KEY`` is a normal column and stays NULL
# on insert (raising "NOT NULL constraint failed: id"). The variant keeps BIGINT
# on Postgres while emitting plain INTEGER on SQLite so local dev autoincrements.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    """Timezone-aware current UTC instant (used as column default)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
