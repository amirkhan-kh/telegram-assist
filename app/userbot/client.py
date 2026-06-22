"""Telethon client construction + headless connection.

The userbot runs the owner's *user account* using a pre-generated
``StringSession`` (created locally with ``python -m scripts.generate_session``).
At runtime we only ever ``connect`` and verify authorization — we NEVER prompt
interactively. If the session is missing/expired we log a clear instruction and
let the caller decide whether to continue without the userbot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


def build_userbot(settings: Settings) -> TelegramClient:
    """Construct (but do not connect) the Telethon userbot client.

    Uses an in-memory ``StringSession`` so nothing touches the filesystem and
    the same session string can be moved between hosts/containers.
    """
    return TelegramClient(
        StringSession(settings.telethon_session),
        settings.api_id,
        settings.api_hash,
    )


async def connect_userbot(client: TelegramClient) -> bool:
    """Connect the userbot and verify it is authorized.

    Returns ``True`` when connected *and* authorized. Returns ``False`` (after
    logging a clear, actionable English error) when the session is absent or no
    longer valid — never raising and never prompting for credentials.
    """
    await client.connect()
    if not await client.is_user_authorized():
        logger.error(
            "userbot.not_authorized",
            hint=(
                "Userbot session is missing or expired. Generate a new one "
                "locally with: python -m scripts.generate_session, then set "
                "TELETHON_SESSION in your .env."
            ),
        )
        return False
    logger.info("userbot.connected")
    return True
