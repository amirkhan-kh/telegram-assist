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

import re
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


def _dialog_name(entity: object) -> str:
    """Best display name for a private dialog entity."""
    first = (getattr(entity, "first_name", None) or "").strip()
    last = (getattr(entity, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    title = (getattr(entity, "title", None) or "").strip()
    if title:
        return title
    return (getattr(entity, "username", None) or "").strip()


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


async def sync_private_dialogs(
    client: TelegramClient, registry: ServiceRegistry, *, limit: int = 700
) -> int:
    """Mirror reachable ALL CHATS private profiles into ``people``.

    Telegram contacts only cover the saved address book. The owner may still
    have private chats with unsaved profiles; these are reachable by userbot and
    should resolve by name for message/chat-history commands.
    """
    upserted = 0
    try:
        async with registry.session() as session:
            async for dialog in client.iter_dialogs(limit=limit):
                entity = getattr(dialog, "entity", None)
                if entity is None:
                    continue
                if getattr(entity, "bot", False) or getattr(entity, "deleted", False):
                    continue
                user_id = getattr(entity, "id", None)
                if user_id is None:
                    continue
                # Only private user dialogs. Channels/groups have titles and ids,
                # but contact commands should not accidentally target them.
                if not hasattr(entity, "first_name") and not hasattr(entity, "last_name"):
                    continue
                name = _dialog_name(entity)
                if not name:
                    continue
                await person_repo.upsert_telegram_contact(
                    session,
                    telegram_user_id=int(user_id),
                    display_name=name,
                    username=getattr(entity, "username", None),
                    phone=getattr(entity, "phone", None),
                )
                upserted += 1
    except Exception as exc:  # noqa: BLE001 - best-effort sync
        logger.warning("userbot.dialogs.sync.error", error=str(exc))
    logger.info("userbot.dialogs.sync.done", count=upserted)
    return upserted


async def import_phone_contact(
    client: TelegramClient, phone: str
) -> dict[str, object] | None:
    """Import a raw phone number as a Telegram contact -> a minimal user dict.

    Lets the owner message a brand-new number ("+998 90 …ga xabar yubor") that is
    not yet in their address book. Returns ``None`` when the number is not on
    Telegram or the import fails — the caller then reports "topilmadi". Adds the
    number to the owner's Telegram contacts as a side effect (that is how a new
    number becomes messageable). Best-effort; never raises.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return None
    normalized = "+" + digits
    try:
        from telethon.tl.functions.contacts import ImportContactsRequest
        from telethon.tl.types import InputPhoneContact

        contact = InputPhoneContact(
            client_id=0, phone=normalized, first_name=normalized, last_name=""
        )
        result = await client(ImportContactsRequest([contact]))
        users = getattr(result, "users", None) or []
        if not users:  # number not registered on Telegram / hidden by privacy
            logger.info("userbot.import_phone.not_on_telegram", phone=normalized)
            return None
        user = users[0]
        logger.info("userbot.import_phone.ok", phone=normalized, user_id=int(user.id))
        return {
            "user_id": int(user.id),
            "name": _display_name(user) or normalized,
            "username": getattr(user, "username", None),
            "phone": getattr(user, "phone", None) or digits,
        }
    except Exception as exc:  # noqa: BLE001 - import is best-effort, never crash
        logger.warning("userbot.import_phone.error", phone=normalized, error=str(exc))
        return None
