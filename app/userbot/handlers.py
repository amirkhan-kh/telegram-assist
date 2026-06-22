"""Inbound update handlers for the userbot.

The userbot listens on the owner's account and ingests posts from the broadcast
channels the account is subscribed to. Each new channel post is upserted as a
:class:`~app.db.models.channel.ChannelPost` with its engagement metrics (views,
forwards, reaction count); :class:`~app.services.digest_service.DigestService`
later scores these to build the digest.

Group / private messages are ignored — only broadcast channels feed the digest.
A handler must never raise out of the Telethon event loop, so the whole body is
guarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telethon import events

from app.logging_conf import get_logger
from app.repositories import channel_repo

if TYPE_CHECKING:
    from telethon import TelegramClient

    from app.registry import ServiceRegistry

logger = get_logger(__name__)


def _count_reactions(message: Any) -> int | None:
    """Sum reaction counts on a message, or ``None`` when there are none."""
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) if reactions else None
    if not results:
        return None
    total = 0
    for r in results:
        total += int(getattr(r, "count", 0) or 0)
    return total


async def _store_post(registry: ServiceRegistry, chat: Any, message: Any) -> None:
    """Upsert one broadcast-channel post (shared by the live handler + backfill)."""
    if not getattr(chat, "broadcast", False):
        return  # only broadcast channels feed the digest
    async with registry.session() as session:
        channel = await channel_repo.get_or_create_by_tg_id(
            session,
            tg_channel_id=int(chat.id),
            username=getattr(chat, "username", None),
            title=getattr(chat, "title", None),
        )
        await channel_repo.upsert_post(
            session,
            channel_id=channel.id,
            tg_message_id=int(message.id),
            posted_at=message.date,
            text=getattr(message, "message", None) or None,
            views=getattr(message, "views", None),
            forwards=getattr(message, "forwards", None),
            reactions_count=_count_reactions(message),
        )
        await channel_repo.update_cursor(session, channel.id, int(message.id))


async def backfill_recent_posts(
    client: TelegramClient,
    registry: ServiceRegistry,
    *,
    per_channel: int = 30,
    max_channels: int = 40,
) -> int:
    """Ingest recent history from subscribed broadcast channels (run at startup).

    Iterates the userbot's dialogs, and for each broadcast channel stores its
    last ``per_channel`` messages so the digest is populated after a restart.
    Returns the number of posts ingested. Never raises.
    """
    ingested = 0
    channels = 0
    try:
        async for dialog in client.iter_dialogs(limit=200):
            entity = dialog.entity
            if not getattr(entity, "broadcast", False):
                continue
            try:
                async for message in client.iter_messages(entity, limit=per_channel):
                    if message is None:
                        continue
                    await _store_post(registry, entity, message)
                    ingested += 1
            except Exception as exc:  # noqa: BLE001 - skip a bad channel, continue
                logger.warning("userbot.backfill.channel_error", error=str(exc))
            channels += 1
            if channels >= max_channels:
                break
    except Exception as exc:  # noqa: BLE001 - backfill is best-effort
        logger.warning("userbot.backfill.error", error=str(exc))
    logger.info("userbot.backfill.done", channels=channels, posts=ingested)
    return ingested


def register_userbot_handlers(
    client: TelegramClient, registry: ServiceRegistry
) -> None:
    """Attach userbot event handlers to ``client``.

    Registers a ``NewMessage`` handler that ingests broadcast-channel posts for
    the digest; everything else is ignored.
    """

    @client.on(events.NewMessage)
    async def _on_new_message(event: events.NewMessage.Event) -> None:
        try:
            if not getattr(event, "is_channel", False):
                return
            chat = await event.get_chat()
            await _store_post(registry, chat, event.message)
        except Exception as exc:  # noqa: BLE001 - never let a handler crash the loop
            logger.warning("userbot.handler.error", error=str(exc))

    logger.info("userbot.handlers.registered")
