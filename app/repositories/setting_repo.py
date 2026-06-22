"""Repository for :class:`app.db.models.setting.Setting` (key/value JSON store)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.setting import Setting


async def get_value(session: AsyncSession, key: str) -> Any | None:
    """Return the JSON value stored under ``key``, or ``None``."""
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalars().first()
    return row.value if row is not None else None


async def set_value(session: AsyncSession, key: str, value: Any) -> Setting:
    """Insert or update the value stored under ``key``."""
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalars().first()
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    await session.flush()
    return row
