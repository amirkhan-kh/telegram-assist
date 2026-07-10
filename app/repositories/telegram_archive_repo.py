"""Repository helpers for the Telegram archive search index."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.telegram_archive import TelegramArchiveDialog, TelegramArchiveMessage


async def upsert_dialog(
    session: AsyncSession,
    *,
    dialog_id: int,
    title: str,
    kind: str,
    username: str | None = None,
    indexed_at: datetime | None = None,
) -> TelegramArchiveDialog:
    result = await session.execute(
        select(TelegramArchiveDialog).where(TelegramArchiveDialog.dialog_id == dialog_id)
    )
    row = result.scalars().first()
    if row is None:
        row = TelegramArchiveDialog(
            dialog_id=dialog_id,
            title=title,
            kind=kind,
            username=username,
            indexed_at=indexed_at,
        )
        session.add(row)
    else:
        row.title = title
        row.kind = kind
        row.username = username
        if indexed_at is not None:
            row.indexed_at = indexed_at
    await session.flush()
    return row


async def get_dialog(
    session: AsyncSession, *, dialog_id: int
) -> TelegramArchiveDialog | None:
    result = await session.execute(
        select(TelegramArchiveDialog).where(TelegramArchiveDialog.dialog_id == dialog_id)
    )
    return result.scalars().first()


async def mark_dialog_indexed(
    session: AsyncSession,
    *,
    dialog_id: int,
    newest_message_id: int | None,
    oldest_message_id: int | None,
    indexed_at: datetime,
    fully_indexed: bool = False,
) -> None:
    row = await get_dialog(session, dialog_id=dialog_id)
    if row is None:
        return
    if newest_message_id is not None and newest_message_id > row.last_indexed_message_id:
        row.last_indexed_message_id = newest_message_id
    if oldest_message_id is not None:
        if row.oldest_indexed_message_id is None:
            row.oldest_indexed_message_id = oldest_message_id
        else:
            row.oldest_indexed_message_id = min(
                row.oldest_indexed_message_id, oldest_message_id
            )
    row.indexed_at = indexed_at
    if fully_indexed:
        row.history_fully_indexed = True
    await session.flush()


async def upsert_message(session: AsyncSession, **fields: Any) -> TelegramArchiveMessage:
    result = await session.execute(
        select(TelegramArchiveMessage).where(
            TelegramArchiveMessage.dialog_id == fields["dialog_id"],
            TelegramArchiveMessage.message_id == fields["message_id"],
        )
    )
    row = result.scalars().first()
    if row is None:
        row = TelegramArchiveMessage(**fields)
        session.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    await session.flush()
    return row


async def update_message_analysis(
    session: AsyncSession,
    *,
    dialog_id: int,
    message_id: int,
    analysis_text: str,
) -> None:
    result = await session.execute(
        select(TelegramArchiveMessage).where(
            TelegramArchiveMessage.dialog_id == dialog_id,
            TelegramArchiveMessage.message_id == message_id,
        )
    )
    row = result.scalars().first()
    if row is not None:
        row.analysis_text = analysis_text[:4000]
        await session.flush()


async def search_messages(
    session: AsyncSession,
    *,
    tokens: Iterable[str],
    chat_kinds: set[str] | None,
    media_kinds: set[str] | None,
    since: datetime | None,
    candidate_limit: int,
) -> list[TelegramArchiveMessage]:
    stmt = select(TelegramArchiveMessage)
    filters = []
    if chat_kinds:
        filters.append(TelegramArchiveMessage.chat_kind.in_(chat_kinds))
    if media_kinds:
        filters.append(TelegramArchiveMessage.media_kind.in_(media_kinds))
    if since is not None:
        filters.append(TelegramArchiveMessage.sent_at >= since)

    token_filters = []
    for token in list(tokens)[:6]:
        like = f"%{token}%"
        token_filters.append(
            or_(
                TelegramArchiveMessage.text.ilike(like),
                TelegramArchiveMessage.analysis_text.ilike(like),
                TelegramArchiveMessage.chat_title.ilike(like),
                TelegramArchiveMessage.sender_label.ilike(like),
            )
        )
    if token_filters:
        filters.append(or_(*token_filters))

    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(TelegramArchiveMessage.sent_at.desc().nullslast()).limit(
        max(1, candidate_limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
