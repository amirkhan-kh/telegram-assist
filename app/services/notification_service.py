"""NotificationService — owner-facing notifications via the control bot.

All notifications go to ``settings.owner_chat_id`` through ``registry.bot``
(python-telegram-bot). Voice notifications synthesize the owner's voice via the
``VoiceService`` when available, otherwise gracefully fall back to text.

Sibling handles (bot, voice_service) are resolved lazily through the registry at
call time so this service is safe to construct before the others exist.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class NotificationService:
    """Sends notifications to the owner (text or voice note)."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def notify_owner(
        self,
        text: str,
        *,
        reply_markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> None:
        """Send a text message to the owner chat via the control bot."""
        bot = self.registry.bot
        owner_chat_id = self.registry.settings.owner_chat_id
        if bot is None:
            logger.warning("notify.owner.no_bot", text=text)
            return
        await bot.send_message(
            chat_id=owner_chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def notify_owner_voice(self, text: str) -> None:
        """Send a voice note (owner's voice) to the owner; fall back to text."""
        voice = self.registry.voice_service
        bot = self.registry.bot
        owner_chat_id = self.registry.settings.owner_chat_id
        if voice is not None and voice.available() and bot is not None:
            ogg_path = await voice.tts_to_voice_note(text)
            if ogg_path:
                # Read off the event loop, then send the bytes.
                data = await asyncio.to_thread(Path(ogg_path).read_bytes)
                await bot.send_voice(chat_id=owner_chat_id, voice=data)
                return
        # Fall back to a plain text notification.
        await self.notify_owner(text)
