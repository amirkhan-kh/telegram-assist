"""Build the python-telegram-bot :class:`Application` for the control bot.

The returned ``Application`` is *unstarted*: :mod:`app.main` drives its lifecycle
manually (``initialize`` -> ``start`` -> ``updater.start_polling``) so the bot can
share a single asyncio loop with the Telethon userbot and APScheduler.

Every handler is gated behind an owner-only chat filter, so the bot only ever
acts on messages from ``settings.owner_chat_id``. The live :class:`ServiceRegistry`
is stashed in ``application.bot_data`` for handlers to read at call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import (
    on_callback,
    on_contact,
    on_error,
    on_help,
    on_photo,
    on_start,
    on_text,
    on_voice,
)
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

    from app.registry import ServiceRegistry

logger = get_logger(__name__)


def build_application(registry: ServiceRegistry) -> Application:
    """Construct the control bot ``Application`` with owner-only handlers."""
    settings = registry.settings
    application = ApplicationBuilder().token(settings.bot_token).build()

    # Handlers reach the live runtime through bot_data (see app.bot.handlers).
    application.bot_data["registry"] = registry

    owner_only = filters.Chat(chat_id=settings.owner_chat_id)

    application.add_handler(CommandHandler("start", on_start, filters=owner_only))
    application.add_handler(CommandHandler("help", on_help, filters=owner_only))
    application.add_handler(
        MessageHandler(owner_only & filters.TEXT & ~filters.COMMAND, on_text)
    )
    application.add_handler(
        MessageHandler(owner_only & (filters.VOICE | filters.AUDIO), on_voice)
    )
    application.add_handler(
        MessageHandler(owner_only & filters.CONTACT, on_contact)
    )
    # Document photos (passport / inspection / insurance) — owner chat only.
    application.add_handler(
        MessageHandler(owner_only & filters.PHOTO, on_photo)
    )
    # Inline-button taps (Done / Snooze / Move / Cancel) — owner chat only.
    application.add_handler(
        CallbackQueryHandler(on_callback, pattern=None)
    )
    application.add_error_handler(on_error)

    logger.info("bot.application.built", owner_chat_id=settings.owner_chat_id)
    return application
