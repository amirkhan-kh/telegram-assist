"""Repository for channel ingest (Milestone 3): ``Channel`` / ``ChannelPost``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.channel import Channel, ChannelPost


async def create(session: AsyncSession, **fields: Any) -> Channel:
    """Create and flush a new channel, returning the refreshed row."""
    channel = Channel(**fields)
    session.add(channel)
    await session.flush()
    await session.refresh(channel)
    return channel


async def get_or_create_by_tg_id(
    session: AsyncSession,
    *,
    tg_channel_id: int,
    username: str | None = None,
    title: str | None = None,
) -> Channel:
    """Return the channel for ``tg_channel_id``, creating it if absent.

    Username/title are refreshed on each call so renamed channels stay current.
    """
    result = await session.execute(
        select(Channel).where(Channel.tg_channel_id == tg_channel_id)
    )
    channel = result.scalars().first()
    if channel is None:
        return await create(
            session,
            tg_channel_id=tg_channel_id,
            username=username,
            title=title,
        )
    changed = False
    if username is not None and channel.username != username:
        channel.username = username
        changed = True
    if title is not None and channel.title != title:
        channel.title = title
        changed = True
    if changed:
        await session.flush()
    return channel


async def list_active(session: AsyncSession) -> list[Channel]:
    """Return all active channels ordered by descending weight."""
    result = await session.execute(
        select(Channel)
        .where(Channel.is_active.is_(True))
        .order_by(Channel.weight.desc())
    )
    return list(result.scalars().all())


async def upsert_post(session: AsyncSession, **fields: Any) -> ChannelPost:
    """Insert a channel post or update the existing one.

    Uniqueness is on ``(channel_id, tg_message_id)``; when a post already
    exists its mutable metric fields are refreshed from ``fields``.
    """
    channel_id = fields["channel_id"]
    tg_message_id = fields["tg_message_id"]

    result = await session.execute(
        select(ChannelPost).where(
            ChannelPost.channel_id == channel_id,
            ChannelPost.tg_message_id == tg_message_id,
        )
    )
    post = result.scalars().first()

    if post is None:
        post = ChannelPost(**fields)
        session.add(post)
    else:
        for key, value in fields.items():
            if key in ("channel_id", "tg_message_id"):
                continue
            setattr(post, key, value)

    await session.flush()
    await session.refresh(post)
    return post


async def update_cursor(
    session: AsyncSession, channel_id: int, last_msg_id: int
) -> Channel | None:
    """Advance a channel's ingest cursor to ``last_msg_id`` (monotonic)."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        return None
    if last_msg_id > channel.last_ingested_message_id:
        channel.last_ingested_message_id = last_msg_id
        await session.flush()
    return channel


async def posts_since(
    session: AsyncSession, since: datetime, *, only_undigested: bool = True
) -> list[ChannelPost]:
    """Return channel posts newer than ``since`` (optionally not yet digested)."""
    stmt = select(ChannelPost).where(ChannelPost.posted_at >= since)
    if only_undigested:
        stmt = stmt.where(ChannelPost.included_in_digest_id.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def channels_by_id(session: AsyncSession) -> dict[int, Channel]:
    """Return all channels keyed by primary key (for score weighting/links)."""
    result = await session.execute(select(Channel))
    return {c.id: c for c in result.scalars().all()}


async def mark_digested(
    session: AsyncSession, post_ids: list[int], digest_id: int
) -> None:
    """Stamp ``digest_id`` onto each post so it is not re-shown in a later digest."""
    for pid in post_ids:
        post = await session.get(ChannelPost, pid)
        if post is not None:
            post.included_in_digest_id = digest_id
    await session.flush()
