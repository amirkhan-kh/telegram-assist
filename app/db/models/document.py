"""DocumentPhoto model — a stored photo of a personal document.

The owner photographs a passport / car inspection / insurance; the bot keeps the
Telegram ``file_id`` so it can re-send the image on request, and links it to the
:class:`app.db.models.event.Event` that carries the extracted expiry date and its
pre-alerts. Stored as its own table (not a column on ``events``) so the schema
add is a brand-new table that ``Base.metadata.create_all`` creates automatically
on the existing Postgres — no ALTER TABLE migration needed.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class DocumentPhoto(Base, TimestampMixin):
    __tablename__ = "document_photos"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("people.id"), index=True
    )
    # "passport" | "inspection" | "insurance" — which document this image is.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # Telegram file_id: re-uploadable forever, so re-sending needs no local copy.
    file_id: Mapped[str] = mapped_column(String(400))
    # The Event that carries the expiry date + its 7/3/1-day pre-alerts (nullable
    # when the date could not be read and the owner has not typed it yet).
    event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("events.id"), nullable=True
    )
