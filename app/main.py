"""Entrypoint — runs the bot, userbot, scheduler and services on ONE asyncio loop.

Startup order (see plan): logging -> settings -> db -> scheduler (built) ->
services wired into the registry -> userbot connected -> bot started ->
scheduler started -> ensure owner -> park until SIGINT/SIGTERM -> graceful
shutdown in reverse.

The loop is owned explicitly via ``asyncio.run`` (not ``Application.run_polling``)
so python-telegram-bot, Telethon and APScheduler can share it.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.config import get_settings
from app.db.engine import create_db_engine, create_sessionmaker
from app.db.models import Base
from app.logging_conf import configure_logging, get_logger
from app.registry import ServiceRegistry, set_registry
from app.repositories import person_repo
from app.scheduler.factory import build_scheduler

log = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.require_runtime()

    # ── database ──────────────────────────────────────────────────────────
    engine = create_db_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    # Ensure the schema exists (idempotent — create_all only adds missing
    # tables). The project ships no Alembic migrations, so this is the schema
    # source of truth for both SQLite (local) and Postgres (Docker).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("schema ensured", sqlite=settings.is_sqlite)

    # ── scheduler (built, not started) ────────────────────────────────────
    scheduler = build_scheduler(settings)

    # ── registry + services ───────────────────────────────────────────────
    registry = ServiceRegistry(
        settings=settings, sessionmaker=sessionmaker, scheduler=scheduler
    )
    set_registry(registry)
    _wire_services(registry)
    await _load_google_secret(registry)

    # ── userbot (Telethon) ────────────────────────────────────────────────
    from app.userbot.client import build_userbot, connect_userbot
    from app.userbot.handlers import register_userbot_handlers
    from app.userbot.sender import UserbotSender

    userbot = build_userbot(settings)
    registry.userbot = userbot
    authorized = await connect_userbot(userbot)
    if not authorized:
        log.warning("userbot not authorized — outbound text/voice disabled until session set")
    registry.sender = UserbotSender(registry)
    register_userbot_handlers(userbot, registry)

    # ── bot (python-telegram-bot) ─────────────────────────────────────────
    from app.bot.application import build_application

    application = build_application(registry)
    await application.initialize()
    await application.start()
    registry.bot = application.bot
    await application.updater.start_polling(drop_pending_updates=True)

    # ── go ────────────────────────────────────────────────────────────────
    scheduler.start()
    await _ensure_owner(registry)
    _schedule_daily_digest(registry)
    _schedule_daily_briefings(registry)

    # Populate recent channel history in the background so the digest is not
    # empty right after a restart (best-effort; never blocks startup).
    backfill_task: asyncio.Task | None = None
    contacts_task: asyncio.Task | None = None
    archive_index_task: asyncio.Task | None = None
    if authorized:
        backfill_task = asyncio.create_task(_run_backfill(registry, userbot))
        # Mirror the owner's phone/Telegram contacts so messages can be
        # addressed by saved contact name (best-effort, in the background).
        contacts_task = asyncio.create_task(_run_contact_sync(registry, userbot))
        archive_index_task = asyncio.create_task(_run_archive_indexer(registry, userbot))

    log.info(
        "telegram-assistant started",
        userbot_authorized=authorized,
        voice=registry.voice_service.available() if registry.voice_service else False,
    )

    stop = _install_stop_event()
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        if backfill_task is not None:
            backfill_task.cancel()
        if contacts_task is not None:
            contacts_task.cancel()
        if archive_index_task is not None:
            archive_index_task.cancel()
        with contextlib.suppress(Exception):
            scheduler.shutdown(wait=False)
        with contextlib.suppress(Exception):
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        with contextlib.suppress(Exception):
            await userbot.disconnect()
        with contextlib.suppress(Exception):
            await engine.dispose()
        log.info("shutdown complete")


def _wire_services(registry: ServiceRegistry) -> None:
    """Construct services and attach them to the registry. Services hold a ref
    to the registry and resolve siblings lazily, so order here is irrelevant."""
    from app.integrations.google.calendar import GoogleCalendarService
    from app.integrations.google.gmail import GmailService
    from app.integrations.google.oauth import get_credentials
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

    settings = registry.settings
    registry.voice_service = VoiceService(settings)
    registry.nlu_service = NluService(registry)
    registry.notification_service = NotificationService(registry)
    registry.reminder_service = ReminderService(registry)
    registry.task_service = TaskService(registry)
    registry.finance_service = FinanceService(registry)
    registry.message_service = MessageService(registry)
    registry.meeting_service = MeetingService(registry)
    registry.digest_service = DigestService(registry)
    registry.secret_service = SecretService(registry)
    registry.event_service = EventService(registry)
    registry.document_service = DocumentService(registry)
    registry.decision_service = DecisionService(registry)
    registry.briefing_service = BriefingService(registry)
    registry.notion_service = NotionService(registry)
    creds = get_credentials(settings)
    registry.calendar_service = GoogleCalendarService(
        creds,
        timezone=settings.user_timezone,
        work_start_hour=settings.work_day_start_hour,
        work_end_hour=settings.work_day_end_hour,
    )
    registry.gmail_service = GmailService(creds)


async def _load_google_secret(registry: ServiceRegistry) -> None:
    """Fall back to a vault-stored Google refresh token when none is in env.

    Lets the encrypted secret store (``SECRETS_ENC_KEY``) supply the Google OAuth
    refresh token without putting it in plaintext env. No-op when the env token
    is set, the vault is unavailable, or no token is stored.
    """
    settings = registry.settings
    if settings.google_oauth_refresh_token:
        return
    secret_service = registry.secret_service
    if secret_service is None or not secret_service.available():
        return
    token = await secret_service.get("google_oauth_refresh_token")
    if not token:
        return

    from app.integrations.google.calendar import GoogleCalendarService
    from app.integrations.google.oauth import get_credentials

    creds = get_credentials(settings, refresh_token=token)
    if creds is not None:
        from app.integrations.google.gmail import GmailService

        registry.calendar_service = GoogleCalendarService(
            creds,
            timezone=settings.user_timezone,
            work_start_hour=settings.work_day_start_hour,
            work_end_hour=settings.work_day_end_hour,
        )
        registry.gmail_service = GmailService(creds)
        log.info("google calendar + gmail credentials loaded from secret store")


async def _run_backfill(registry: ServiceRegistry, userbot: object) -> None:
    """Background channel-history backfill (best-effort; errors are swallowed)."""
    from app.userbot.handlers import backfill_recent_posts

    settings = registry.settings
    with contextlib.suppress(asyncio.CancelledError):
        await backfill_recent_posts(
            userbot,  # type: ignore[arg-type]
            registry,
            per_channel=settings.digest_backfill_per_channel,
            max_channels=settings.digest_backfill_max_channels,
        )


async def _run_contact_sync(registry: ServiceRegistry, userbot: object) -> None:
    """Background sync of the owner's Telegram contacts (best-effort)."""
    from app.userbot.contacts import sync_contacts, sync_private_dialogs

    with contextlib.suppress(asyncio.CancelledError):
        await sync_contacts(userbot, registry)  # type: ignore[arg-type]
        await sync_private_dialogs(userbot, registry)  # type: ignore[arg-type]


async def _run_archive_indexer(registry: ServiceRegistry, userbot: object) -> None:
    """Continuously refresh the local Telegram archive index in the background."""
    from app.services.telegram_archive_indexer import run_archive_index_cycle

    interval = max(60, registry.settings.jarvis_archive_index_interval_seconds)
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            await run_archive_index_cycle(registry, userbot)
            await asyncio.sleep(interval)


def _schedule_daily_digest(registry: ServiceRegistry) -> None:
    """Register the recurring daily channel digest, or remove it when disabled.

    A negative hour disables the auto digest; we also *remove* any previously
    persisted job so a stale digest:daily from an earlier run can't keep firing.
    """
    from app.scheduler.jobs import (
        DAILY_DIGEST_JOB_ID,
        cancel_job,
        schedule_daily_digest,
    )

    hour = registry.settings.digest_daily_hour
    if hour is None or hour < 0:
        cancel_job(registry.scheduler, DAILY_DIGEST_JOB_ID)
        return
    schedule_daily_digest(
        registry.scheduler, hour=hour, timezone=registry.settings.user_timezone
    )


def _schedule_daily_briefings(registry: ServiceRegistry) -> None:
    """Register the morning plan + evening review, or remove them when disabled."""
    from app.scheduler.jobs import (
        EVENING_REVIEW_JOB_ID,
        MORNING_BRIEFING_JOB_ID,
        cancel_job,
        schedule_daily_briefing,
        schedule_evening_review,
    )

    settings = registry.settings
    tz = settings.user_timezone
    if settings.morning_briefing_hour is not None and settings.morning_briefing_hour >= 0:
        schedule_daily_briefing(
            registry.scheduler, hour=settings.morning_briefing_hour, timezone=tz
        )
    else:
        cancel_job(registry.scheduler, MORNING_BRIEFING_JOB_ID)
    if settings.evening_review_hour is not None and settings.evening_review_hour >= 0:
        schedule_evening_review(
            registry.scheduler, hour=settings.evening_review_hour, timezone=tz
        )
    else:
        cancel_job(registry.scheduler, EVENING_REVIEW_JOB_ID)


async def _ensure_owner(registry: ServiceRegistry) -> None:
    settings = registry.settings
    async with registry.session() as session:
        await person_repo.ensure_owner(
            session,
            telegram_user_id=settings.owner_chat_id,
            display_name="Owner",
        )


def _install_stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop


if __name__ == "__main__":
    asyncio.run(main())
