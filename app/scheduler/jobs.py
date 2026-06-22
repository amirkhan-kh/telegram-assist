"""Persisted job callable + scheduling helpers.

APScheduler's jobstore can only serialise a reference to a module-level function
plus JSON args — never the live ``Bot`` / ``TelegramClient`` / services. So every
domain job is stored as the string ``app.scheduler.jobs:execute_job`` with
``args=[kind, row_id, role]``. When it fires, :func:`execute_job` reaches the
live runtime through :func:`app.registry.get_registry` and dispatches on
:class:`~app.db.models.enums.ScheduleKind` to the right service coroutine.

Services schedule and cancel jobs exclusively through :func:`schedule_at` and
:func:`cancel_job`; both compute stable ids via :func:`make_job_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.db.models.enums import ScheduleKind
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = get_logger(__name__)

# Dotted path APScheduler persists in the jobstore for every domain job.
_JOB_FUNC = "app.scheduler.jobs:execute_job"


# ── persisted callable ──────────────────────────────────────────────────────
async def execute_job(kind: str, row_id: int, role: str = "") -> None:
    """Dispatch a persisted job to the matching live service coroutine.

    Resolves the runtime ``ServiceRegistry`` and routes on ``kind``. The whole
    body is guarded: a job must never raise out of the scheduler executor, so
    every failure is logged and swallowed.
    """
    from app.registry import get_registry

    try:
        registry = get_registry()
    except RuntimeError:
        logger.error("job.no_registry", kind=kind, row_id=row_id, role=role)
        return

    try:
        schedule_kind = ScheduleKind(kind)
    except ValueError:
        logger.error("job.unknown_kind", kind=kind, row_id=row_id, role=role)
        return

    try:
        await _dispatch(registry, schedule_kind, row_id, role)
    except Exception:  # noqa: BLE001 - jobs must never raise out of the executor
        logger.exception(
            "job.failed", kind=kind, row_id=row_id, role=role
        )


async def _dispatch(
    registry: object, kind: ScheduleKind, row_id: int, role: str
) -> None:
    """Route a resolved ``ScheduleKind`` to its service method.

    Missing services are logged and skipped (e.g. before full wiring or when an
    optional handle such as the digest service is absent).
    """
    if kind is ScheduleKind.reminder:
        service = getattr(registry, "reminder_service", None)
        if service is not None:
            await service.fire(row_id, role)
            return
    elif kind is ScheduleKind.promise_alert:
        service = getattr(registry, "task_service", None)
        if service is not None:
            await service.fire_promise_alert(row_id, role)
            return
    elif kind is ScheduleKind.followup_owner:
        service = getattr(registry, "task_service", None)
        if service is not None:
            await service.fire_followup_owner(row_id, role)
            return
    elif kind is ScheduleKind.followup_assignee:
        service = getattr(registry, "task_service", None)
        if service is not None:
            await service.fire_followup_assignee(row_id, role)
            return
    elif kind is ScheduleKind.scheduled_message:
        service = getattr(registry, "message_service", None)
        if service is not None:
            await service.send_now(row_id)
            return
    elif kind is ScheduleKind.meeting_alert:
        service = getattr(registry, "meeting_service", None)
        if service is not None:
            await service.fire_alert(row_id, role)
            return
    elif kind is ScheduleKind.meeting_link:
        service = getattr(registry, "meeting_service", None)
        if service is not None:
            await service.deliver_link(row_id)
            return
    elif kind is ScheduleKind.debt_reminder:
        service = getattr(registry, "finance_service", None)
        if service is not None:
            await service.fire_debt_reminder(row_id, role)
            return
    elif kind is ScheduleKind.digest:
        digest_service = getattr(registry, "digest_service", None)
        if digest_service is None:
            extras = getattr(registry, "extras", {}) or {}
            digest_service = extras.get("digest_service")
        if digest_service is not None:
            await digest_service.run()
        else:
            logger.info("job.digest.no_service", row_id=row_id)
        return
    elif kind is ScheduleKind.important_date:
        service = getattr(registry, "event_service", None)
        if service is not None:
            await service.fire(row_id, role)
            return
    elif kind is ScheduleKind.morning_briefing:
        service = getattr(registry, "briefing_service", None)
        if service is not None:
            await service.run_morning()
            return
    elif kind is ScheduleKind.evening_review:
        service = getattr(registry, "briefing_service", None)
        if service is not None:
            await service.run_evening()
            return

    logger.warning("job.no_service", kind=kind.value, row_id=row_id, role=role)


# ── scheduling helpers ──────────────────────────────────────────────────────
def make_job_id(kind: ScheduleKind, row_id: int, role: str = "") -> str:
    """Build a stable, human-readable job id.

    Including the role lets a single row own several distinct jobs (e.g. a
    reminder's pre-alert and deadline) without id collisions, while keeping
    ``replace_existing`` idempotent for re-scheduling the same logical job.
    """
    base = f"{kind.value}:{row_id}"
    return f"{base}:{role}" if role else base


def schedule_at(
    scheduler: AsyncIOScheduler,
    *,
    kind: ScheduleKind,
    row_id: int,
    run_at: datetime,
    role: str = "",
    job_id: str | None = None,
) -> str:
    """Register a one-shot ``DateTrigger`` job for ``execute_job`` and return its id.

    The job persists ``args=[kind.value, row_id, role]`` so it survives restarts.
    ``replace_existing`` makes re-scheduling the same logical job idempotent.
    """
    resolved_id = job_id or make_job_id(kind, row_id, role)
    scheduler.add_job(
        _JOB_FUNC,
        trigger=DateTrigger(run_date=run_at),
        args=[kind.value, row_id, role],
        id=resolved_id,
        replace_existing=True,
    )
    logger.info(
        "job.scheduled",
        job_id=resolved_id,
        kind=kind.value,
        row_id=row_id,
        role=role,
        run_at=run_at.isoformat(),
    )
    return resolved_id


def schedule_cron(
    scheduler: AsyncIOScheduler,
    *,
    kind: ScheduleKind,
    row_id: int,
    cron_fields: dict,
    timezone: str = "UTC",
    role: str = "",
    job_id: str | None = None,
) -> str:
    """Register a *recurring* ``CronTrigger`` job for ``execute_job`` and return its id.

    ``cron_fields`` is passed straight to :class:`CronTrigger` (e.g.
    ``{"day_of_week": "mon", "hour": 8, "minute": 0}`` for "every Monday 08:00",
    or ``{"day": "last", "hour": 9}`` for "month end"). Like :func:`schedule_at`
    the job persists ``args=[kind.value, row_id, role]`` so it survives restarts.
    """
    resolved_id = job_id or make_job_id(kind, row_id, role)
    scheduler.add_job(
        _JOB_FUNC,
        trigger=CronTrigger(timezone=ZoneInfo(timezone), **cron_fields),
        args=[kind.value, row_id, role],
        id=resolved_id,
        replace_existing=True,
    )
    logger.info(
        "job.scheduled.cron",
        job_id=resolved_id,
        kind=kind.value,
        row_id=row_id,
        role=role,
        cron=cron_fields,
    )
    return resolved_id


def cancel_job(scheduler: AsyncIOScheduler, job_id: str) -> None:
    """Remove a scheduled job; silently ignore an already-gone job."""
    try:
        scheduler.remove_job(job_id)
        logger.info("job.cancelled", job_id=job_id)
    except JobLookupError:
        logger.debug("job.cancel.missing", job_id=job_id)


def next_cron_run(
    cron_fields: dict, timezone: str, after: datetime
) -> datetime | None:
    """Return the next UTC fire time for ``cron_fields`` strictly after ``after``.

    Used to seed a recurring reminder's ``due_at`` for display without waiting for
    its first firing. Returns ``None`` if the cron never fires again.
    """
    trigger = CronTrigger(timezone=ZoneInfo(timezone), **cron_fields)
    fire = trigger.get_next_fire_time(None, after)
    return fire.astimezone(UTC) if fire is not None else None


# ── recurring jobs ──────────────────────────────────────────────────────────
DAILY_DIGEST_JOB_ID = "digest:daily"


def schedule_daily_digest(
    scheduler: AsyncIOScheduler, *, hour: int, timezone: str = "UTC"
) -> str:
    """Register (idempotently) a daily channel-digest cron job at ``hour`` local.

    Fires ``execute_job("digest", 0, "")`` once a day, which builds and delivers
    the digest to the owner. ``replace_existing`` keeps it idempotent across
    restarts; the ``row_id`` is unused by the digest handler.
    """
    scheduler.add_job(
        _JOB_FUNC,
        trigger=CronTrigger(hour=hour, minute=0, timezone=ZoneInfo(timezone)),
        args=[ScheduleKind.digest.value, 0, ""],
        id=DAILY_DIGEST_JOB_ID,
        replace_existing=True,
    )
    logger.info("job.daily_digest.scheduled", hour=hour, timezone=timezone)
    return DAILY_DIGEST_JOB_ID


MORNING_BRIEFING_JOB_ID = "briefing:morning"
EVENING_REVIEW_JOB_ID = "briefing:evening"


def schedule_daily_briefing(
    scheduler: AsyncIOScheduler, *, hour: int, timezone: str = "UTC"
) -> str:
    """Register (idempotently) the daily morning-plan cron job at ``hour`` local."""
    scheduler.add_job(
        _JOB_FUNC,
        trigger=CronTrigger(hour=hour, minute=0, timezone=ZoneInfo(timezone)),
        args=[ScheduleKind.morning_briefing.value, 0, ""],
        id=MORNING_BRIEFING_JOB_ID,
        replace_existing=True,
    )
    logger.info("job.morning_briefing.scheduled", hour=hour, timezone=timezone)
    return MORNING_BRIEFING_JOB_ID


def schedule_evening_review(
    scheduler: AsyncIOScheduler, *, hour: int, timezone: str = "UTC"
) -> str:
    """Register (idempotently) the nightly day-end review cron job at ``hour`` local."""
    scheduler.add_job(
        _JOB_FUNC,
        trigger=CronTrigger(hour=hour, minute=0, timezone=ZoneInfo(timezone)),
        args=[ScheduleKind.evening_review.value, 0, ""],
        id=EVENING_REVIEW_JOB_ID,
        replace_existing=True,
    )
    logger.info("job.evening_review.scheduled", hour=hour, timezone=timezone)
    return EVENING_REVIEW_JOB_ID
