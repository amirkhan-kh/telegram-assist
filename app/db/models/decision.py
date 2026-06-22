"""Decision model — the owner's personal decisions journal ("qarorlar arxivi").

When the owner records a decision ("bugun qaror qildim: …") it is appended here
with a timestamp and an optional tag, so it can be reviewed later as an archive.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin, utcnow


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("people.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    tag: Mapped[str | None] = mapped_column(String(80), nullable=True)
