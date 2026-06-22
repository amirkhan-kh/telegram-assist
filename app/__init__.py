"""Telegram Assistant — personal bot + userbot assistant.

Package layout:
    config         pydantic-settings configuration
    registry       ServiceRegistry singleton (live bot/userbot/scheduler/db bridge)
    db             SQLAlchemy async engine + ORM models
    repositories   data-access layer (one per aggregate)
    scheduler      APScheduler factory + serializable job dispatcher
    brain          Claude NLU: intents, tools, router, time/contact resolution
    services       business logic (reminder, task, finance, voice, meeting, digest...)
    bot            python-telegram-bot control panel
    userbot        Telethon client acting as the user (send text/voice, read channels)
    integrations   thin external API clients (Anthropic, ElevenLabs, Google)
"""

__version__ = "0.1.0"
