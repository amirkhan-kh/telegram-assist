"""Repository for :class:`app.db.models.document.DocumentPhoto`.

Plain async helpers to store a document photo, fetch the latest one per kind (to
re-send on request), and list them for the owner.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import DocumentPhoto


async def create(
    session: AsyncSession,
    *,
    owner_id: int,
    kind: str,
    file_id: str,
    event_id: int | None = None,
) -> DocumentPhoto:
    """Store a document photo and return the flushed row."""
    photo = DocumentPhoto(
        owner_id=owner_id, kind=kind, file_id=file_id, event_id=event_id
    )
    session.add(photo)
    await session.flush()
    await session.refresh(photo)
    return photo


async def latest_by_kind(
    session: AsyncSession, owner_id: int, kind: str
) -> DocumentPhoto | None:
    """The most recently stored photo for one document kind, or ``None``."""
    result = await session.execute(
        select(DocumentPhoto)
        .where(DocumentPhoto.owner_id == owner_id, DocumentPhoto.kind == kind)
        .order_by(DocumentPhoto.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def list_latest_per_kind(
    session: AsyncSession, owner_id: int
) -> list[DocumentPhoto]:
    """One photo per kind (the latest), newest kinds first — for a gallery view."""
    result = await session.execute(
        select(DocumentPhoto)
        .where(DocumentPhoto.owner_id == owner_id)
        .order_by(DocumentPhoto.id.desc())
    )
    seen: set[str] = set()
    out: list[DocumentPhoto] = []
    for photo in result.scalars().all():
        if photo.kind in seen:
            continue
        seen.add(photo.kind)
        out.append(photo)
    return out


async def set_event_id(
    session: AsyncSession, photo_id: int, event_id: int
) -> None:
    """Link a stored photo to the Event that carries its expiry + alerts."""
    photo = await session.get(DocumentPhoto, photo_id)
    if photo is not None:
        photo.event_id = event_id
        await session.flush()


async def delete_by_kind(
    session: AsyncSession, owner_id: int, kind: str
) -> list[int]:
    """Delete every stored photo of one kind; return their linked event ids.

    Used when the owner replaces or deletes a document — the caller then cancels
    those events so no stale expiry alerts survive.
    """
    result = await session.execute(
        select(DocumentPhoto).where(
            DocumentPhoto.owner_id == owner_id, DocumentPhoto.kind == kind
        )
    )
    rows = list(result.scalars().all())
    event_ids = [r.event_id for r in rows if r.event_id is not None]
    for row in rows:
        await session.delete(row)
    await session.flush()
    return event_ids
