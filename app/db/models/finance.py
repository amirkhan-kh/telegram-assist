"""Finance model — debts/credits (who owes the user, whom the user owes)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import DebtDirection, DebtStatus


class DebtRecord(Base, TimestampMixin):
    __tablename__ = "debt_records"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    counterparty_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("people.id"), index=True
    )
    direction: Mapped[DebtDirection] = mapped_column(
        SAEnum(DebtDirection, native_enum=False, length=16)
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="UZS")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    incurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[DebtStatus] = mapped_column(
        SAEnum(DebtStatus, native_enum=False, length=16),
        default=DebtStatus.open,
        index=True,
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
