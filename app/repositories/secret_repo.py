"""Repository for :class:`app.db.models.setting.Secret` (encrypted key/value)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.setting import Secret


async def get(session: AsyncSession, name: str) -> Secret | None:
    """Return the secret row for ``name``, or ``None``."""
    result = await session.execute(select(Secret).where(Secret.name == name))
    return result.scalars().first()


async def upsert(
    session: AsyncSession, *, name: str, value_encrypted: bytes
) -> Secret:
    """Insert or update the encrypted value for ``name``."""
    secret = await get(session, name)
    if secret is None:
        secret = Secret(name=name, value_encrypted=value_encrypted)
        session.add(secret)
    else:
        secret.value_encrypted = value_encrypted
    await session.flush()
    await session.refresh(secret)
    return secret


async def delete(session: AsyncSession, name: str) -> bool:
    """Delete the secret named ``name``; return True if a row was removed."""
    secret = await get(session, name)
    if secret is None:
        return False
    await session.delete(secret)
    await session.flush()
    return True
