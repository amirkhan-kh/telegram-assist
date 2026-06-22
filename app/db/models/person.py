"""Person / contact model."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin
from app.db.models.enums import SendMode


class Person(Base, TimestampMixin):
    """A contact the assistant knows / can message. The single owner has
    ``is_owner=True``."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255), index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # alternative names / spellings used for fuzzy resolution ("Amirxon"/"Amirhon")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    honorific: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "aka"/"opa"
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    default_send_mode: Mapped[SendMode] = mapped_column(
        SAEnum(SendMode, native_enum=False, length=16), default=SendMode.voice
    )
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Person id={self.id} {self.display_name!r}>"
