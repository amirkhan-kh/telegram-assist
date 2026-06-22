"""APScheduler factory.

Builds an ``AsyncIOScheduler`` backed by a SQLAlchemy jobstore that points at
the *same* database as the domain ORM (via the synchronous DSN APScheduler
requires). The scheduler is returned **unstarted** — the caller (``app.main``)
starts it once the event loop is running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Build an unstarted ``AsyncIOScheduler`` with a persistent jobstore.

    Jobs persist in the ``apscheduler_jobs`` table of the same database the app
    uses (addressed through the sync DSN). Defaults coalesce missed runs and
    allow a generous misfire grace window so a restart does not drop due jobs.
    """
    jobstore = SQLAlchemyJobStore(
        url=settings.sync_database_url,
        tablename="apscheduler_jobs",
    )
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 3600,
            "max_instances": 1,
        },
        timezone="UTC",
    )
    logger.info("scheduler.built", sync_url=settings.sync_database_url)
    return scheduler
