"""ServiceRegistry — the bridge between APScheduler's serialized world and the
live runtime objects (bot, userbot, scheduler, db, services).

APScheduler's jobstore can only persist a reference to a module-level function
plus JSON-serializable args; it cannot pickle the live ``Bot`` / ``TelegramClient``.
So scheduled jobs are stored as ``app.scheduler.jobs:execute_job`` with
``args=[kind, row_id]`` and, when they fire, reach everything they need through
this module-level singleton, which is populated once during startup.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycles / heavy imports at module load
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from telegram import Bot
    from telethon import TelegramClient

    from app.config import Settings
    from app.integrations.google.calendar import GoogleCalendarService
    from app.integrations.google.gmail import GmailService
    from app.services.briefing_service import BriefingService
    from app.services.decision_service import DecisionService
    from app.services.digest_service import DigestService
    from app.services.document_service import DocumentService
    from app.services.event_service import EventService
    from app.services.finance_service import FinanceService
    from app.services.meeting_service import MeetingService
    from app.services.message_service import MessageService
    from app.services.nlu_service import NluService
    from app.services.notification_service import NotificationService
    from app.services.notion_service import NotionService
    from app.services.reminder_service import ReminderService
    from app.services.secret_service import SecretService
    from app.services.task_service import TaskService
    from app.services.voice_service import VoiceService
    from app.userbot.sender import UserbotSender


@dataclass
class ServiceRegistry:
    """Holds live singletons. Constructed in ``app.main`` during startup."""

    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    scheduler: AsyncIOScheduler

    # Telegram handles (set after the clients are created/started)
    bot: Bot | None = None
    userbot: TelegramClient | None = None

    # Services (attached after construction; see app.main)
    sender: UserbotSender | None = None
    nlu_service: NluService | None = None
    notification_service: NotificationService | None = None
    reminder_service: ReminderService | None = None
    task_service: TaskService | None = None
    finance_service: FinanceService | None = None
    meeting_service: MeetingService | None = None
    message_service: MessageService | None = None
    voice_service: VoiceService | None = None
    digest_service: DigestService | None = None
    secret_service: SecretService | None = None
    event_service: EventService | None = None
    document_service: DocumentService | None = None
    decision_service: DecisionService | None = None
    briefing_service: BriefingService | None = None
    notion_service: NotionService | None = None

    # Milestone 3 integrations
    calendar_service: GoogleCalendarService | None = None
    gmail_service: GmailService | None = None

    # arbitrary extra handles
    extras: dict = field(default_factory=dict)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Open an async DB session (commits on success, rolls back on error)."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


# ── module-level singleton ────────────────────────────────────────────────
_registry: ServiceRegistry | None = None


def set_registry(registry: ServiceRegistry) -> None:
    global _registry
    _registry = registry


def get_registry() -> ServiceRegistry:
    if _registry is None:
        raise RuntimeError("ServiceRegistry not initialised. Call set_registry() in startup.")
    return _registry
