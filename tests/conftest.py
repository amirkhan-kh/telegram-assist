"""Shared pytest fixtures.

Tests run fully offline against an isolated, temporary SQLite database — no live
Telegram / Anthropic / ElevenLabs calls. Required runtime settings are injected
into the environment *before* ``app.config`` is imported so ``get_settings``
validates cleanly.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

# ── configure settings before importing any app module that reads them ────────
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="ta_test_")
os.close(_DB_FD)
os.remove(_DB_PATH)  # SQLAlchemy creates it; we just want a unique, clean path

os.environ.update(
    BOT_TOKEN="123456:TEST-TOKEN",
    OWNER_CHAT_ID="111222333",
    API_ID="1234567",
    API_HASH="0123456789abcdef0123456789abcdef",
    DATABASE_URL=f"sqlite+aiosqlite:///{_DB_PATH}",
    LLM_PROVIDER="gemini",
    ANTHROPIC_API_KEY="",
    GEMINI_API_KEY="",
    # Keep tests fully offline regardless of the real .env (which may enable Vertex).
    GEMINI_USE_VERTEX="false",
    GOOGLE_APPLICATION_CREDENTIALS="",
    GOOGLE_CLOUD_PROJECT="",
    ELEVENLABS_API_KEY="",
    SECRETS_ENC_KEY=Fernet.generate_key().decode(),
    TEST_MODE="true",
)

from app.config import get_settings  # noqa: E402
from app.db.engine import create_db_engine, create_sessionmaker  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.registry import ServiceRegistry, set_registry  # noqa: E402
from app.repositories import person_repo  # noqa: E402
from app.scheduler.factory import build_scheduler  # noqa: E402

get_settings.cache_clear()

OWNER_CHAT_ID = 111222333


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture
async def engine(settings):
    """A fresh schema on the temp SQLite DB for each test (full isolation)."""
    eng = create_db_engine(settings.database_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def registry(settings, engine):
    """A registry with all domain services wired and the owner present.

    The scheduler is built but left unstarted: ``schedule_at`` queues jobs as
    pending without touching the jobstore, so service calls work without a live
    scheduler loop.
    """
    from app.services.briefing_service import BriefingService
    from app.services.decision_service import DecisionService
    from app.services.digest_service import DigestService
    from app.services.event_service import EventService
    from app.services.finance_service import FinanceService
    from app.services.meeting_service import MeetingService
    from app.services.message_service import MessageService
    from app.services.nlu_service import NluService
    from app.services.notification_service import NotificationService
    from app.services.reminder_service import ReminderService
    from app.services.secret_service import SecretService
    from app.services.task_service import TaskService
    from app.services.voice_service import VoiceService

    sessionmaker = create_sessionmaker(engine)
    reg = ServiceRegistry(
        settings=settings,
        sessionmaker=sessionmaker,
        scheduler=build_scheduler(settings),
    )
    reg.voice_service = VoiceService(settings)
    reg.nlu_service = NluService(reg)
    reg.notification_service = NotificationService(reg)
    reg.reminder_service = ReminderService(reg)
    reg.task_service = TaskService(reg)
    reg.finance_service = FinanceService(reg)
    reg.message_service = MessageService(reg)
    reg.meeting_service = MeetingService(reg)
    reg.digest_service = DigestService(reg)
    reg.secret_service = SecretService(reg)
    reg.event_service = EventService(reg)
    reg.decision_service = DecisionService(reg)
    reg.briefing_service = BriefingService(reg)
    # calendar_service is intentionally left None: tests exercise the
    # graceful no-Google path (meetings still get their 1d/1h/0 alerts).
    set_registry(reg)

    async with reg.session() as session:
        await person_repo.ensure_owner(
            session, telegram_user_id=OWNER_CHAT_ID, display_name="Owner"
        )
    return reg
