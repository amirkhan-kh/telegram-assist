"""Setting (key/value) and Secret (encrypted) models."""

from __future__ import annotations

from sqlalchemy import JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class Setting(Base, TimestampMixin):
    """User-toggleable runtime config (default send mode, quiet hours, etc.)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class Secret(Base, TimestampMixin):
    """Sensitive values acquired at runtime (e.g. Google refresh token),
    Fernet-encrypted with ``SECRETS_ENC_KEY``."""

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
