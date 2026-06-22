"""UserbotSender — the high-level outbound API used by domain services.

Services (task follow-ups, scheduled messages, meeting links) call
:meth:`UserbotSender.send` with a :class:`~app.db.models.enums.SendMode`. This
class turns that into the right Telethon call on the owner's account, applying
the safety guards (rate limiting + FloodWait retry) to every send.

Voice notes are produced by the :class:`~app.services.voice_service.VoiceService`
when available (TTS -> Ogg/Opus), then uploaded as a Telegram voice note. If
voice is unavailable we degrade gracefully to text.

The Telethon client and the voice service are resolved *lazily* through the
registry at call time, so this sender is safe to construct before those handles
exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.db.models.enums import SendMode
from app.logging_conf import get_logger
from app.userbot.safety import RateLimiter

if TYPE_CHECKING:
    from telethon import TelegramClient

    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class UserbotSender:
    """Send messages/voice notes *as the owner* via the Telethon userbot."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        settings = registry.settings
        self._limiter = RateLimiter(
            min_interval=settings.userbot_min_seconds_between_sends,
            daily_limit=settings.userbot_daily_send_limit,
        )

    # ── handles (lazy) ────────────────────────────────────────────────────
    @property
    def _client(self) -> TelegramClient:
        client = self.registry.userbot
        if client is None:
            raise RuntimeError("Userbot client is not initialised.")
        return client

    # ── public API ────────────────────────────────────────────────────────
    async def send(self, chat: Any, content: str, delivery: SendMode) -> None:
        """Dispatch ``content`` to ``chat`` according to ``delivery`` mode."""
        if delivery == SendMode.text:
            await self.send_text(chat, content)
        elif delivery == SendMode.voice:
            await self.send_voice(chat, text=content)
        elif delivery == SendMode.both:
            await self.send_text(chat, content)
            await self.send_voice(chat, text=content)
        else:  # defensive: unknown mode falls back to text
            logger.warning("userbot.send.unknown_mode", mode=str(delivery))
            await self.send_text(chat, content)

    async def send_text(self, chat: Any, text: str) -> None:
        """Send a plain text message, with typing action + safety guards."""
        await self._limiter.acquire(chat)
        client = self._client
        async with client.action(chat, "typing"):
            await RateLimiter.with_flood_retry(
                lambda: client.send_message(chat, text)
            )
        logger.info("userbot.sent.text", chat=str(chat), chars=len(text))

    async def send_voice(
        self,
        chat: Any,
        *,
        text: str | None = None,
        ogg_path: str | None = None,
    ) -> None:
        """Send a voice note.

        Priority:
          1. a pre-rendered ``ogg_path`` is uploaded as a voice note;
          2. otherwise ``text`` is synthesized to Ogg/Opus via the voice service
             (when available) and uploaded as a voice note;
          3. otherwise we fall back to a plain text message.
        """
        path = ogg_path
        if path is None and text is not None:
            voice = self.registry.voice_service
            if voice is not None and voice.available():
                path = await voice.tts_to_voice_note(text)

        if path is None:
            if text is None:
                logger.warning("userbot.send_voice.nothing_to_send", chat=str(chat))
                return
            logger.info("userbot.send_voice.fallback_text", chat=str(chat))
            await self.send_text(chat, text)
            return

        await self._limiter.acquire(chat)
        client = self._client
        async with client.action(chat, "record-audio"):
            await RateLimiter.with_flood_retry(
                lambda: client.send_file(chat, path, voice_note=True)
            )
        logger.info("userbot.sent.voice", chat=str(chat), ogg=path)
