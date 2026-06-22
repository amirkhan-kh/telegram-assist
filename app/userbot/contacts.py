"""Sync the owner's Telegram address book into the ``Person`` table.

The userbot runs as the owner's own account, so it can read the owner's saved
contacts — the same names the owner has stored in their phone (Telegram syncs
the phone address book). We mirror those contacts into the ``people`` table so
the dispatcher's name resolution (``app.brain.contacts.resolve_contact``, which
searches the DB) can map a spoken name like "Akmal aka" onto a concrete
Telegram user to message.

This runs best-effort at startup and on demand (when a recipient name is not
found locally). It never raises into the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.repositories import person_repo

if TYPE_CHECKING:
    from telethon import TelegramClient

    from app.registry import ServiceRegistry

logger = get_logger(__name__)


def _display_name(user: object) -> str:
    """Build the saved contact name from first/last name (fallback: username)."""
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    return (getattr(user, "username", None) or "").strip()


async def sync_contacts(client: TelegramClient, registry: ServiceRegistry) -> int:
    """Mirror the owner's Telegram contacts into the DB. Returns rows upserted.

    Best-effort: a Telegram/API error is logged and swallowed (returns the count
    upserted so far). Bots/deleted accounts and nameless entries are skipped.
    """
    upserted = 0
    try:
        from telethon.tl.functions.contacts import GetContactsRequest

        result = await client(GetContactsRequest(hash=0))
        users = getattr(result, "users", None)
        if not users:  # ContactsNotModified or empty address book
            logger.info("userbot.contacts.sync.empty")
            return 0

        async with registry.session() as session:
            for user in users:
                if getattr(user, "bot", False) or getattr(user, "deleted", False):
                    continue
                name = _display_name(user)
                if not name:
                    continue
                await person_repo.upsert_telegram_contact(
                    session,
                    telegram_user_id=int(user.id),
                    display_name=name,
                    username=getattr(user, "username", None),
                    phone=getattr(user, "phone", None),
                )
                upserted += 1
    except Exception as exc:  # noqa: BLE001 - contact sync must never crash startup
        logger.warning("userbot.contacts.sync.error", error=str(exc))
    logger.info("userbot.contacts.sync.done", count=upserted)
    return upserted
