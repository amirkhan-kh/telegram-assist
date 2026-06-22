"""ReminderService — create reminders, schedule pre-alerts + deadline jobs,
fire them to the owner, and cancel them.

Scheduling goes through ``app.scheduler.jobs`` (imported lazily so this module
stays importable before the scheduler layer exists). Pre-alert jobs use
``role="pre_alert"`` and the due job uses ``role="deadline"``; the due job id is
persisted on the reminder row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.enums import ReminderStatus, ScheduleKind, Source
from app.db.models.reminder import Reminder
from app.logging_conf import get_logger
from app.repositories import reminder_repo
from app.services._timeutil import to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class ReminderService:
    """Owner reminders with optional pre-alerts."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def create_reminder(
        self,
        *,
        owner_id: int,
        title: str,
        when_dt: datetime,
        body: str | None = None,
        pre_alerts_minutes: list[int] | None = None,
        recurrence: str | None = None,
        cron_fields: dict | None = None,
        source: Source = Source.nlu,
    ) -> Reminder:
        """Create a reminder row and schedule its jobs.

        A one-shot reminder schedules pre-alert + deadline date jobs. A *recurring*
        reminder (``cron_fields`` given) schedules a single repeating cron job with
        ``role="recurring"`` instead, so it fires on every occurrence without ever
        marking itself ``fired``. ``recurrence`` stores a human label on the row.
        """
        from app.scheduler.jobs import schedule_at, schedule_cron

        async with self.registry.session() as session:
            reminder = await reminder_repo.create(
                session,
                owner_id=owner_id,
                title=title,
                body=body,
                due_at=when_dt,
                recurrence=recurrence,
                status=ReminderStatus.pending,
                source=source,
            )
            rid = reminder.id

        scheduler = self.registry.scheduler
        now = utcnow()

        # ── recurring reminder: one repeating cron job, no pre-alerts ──────────
        if cron_fields:
            cron_job_id = schedule_cron(
                scheduler,
                kind=ScheduleKind.reminder,
                row_id=rid,
                cron_fields=cron_fields,
                timezone=self.registry.settings.user_timezone,
                role="recurring",
            )
            async with self.registry.session() as session:
                await reminder_repo.set_job_id(session, rid, cron_job_id)
            logger.info("reminder.created.recurring", reminder_id=rid, cron=cron_fields)
            async with self.registry.session() as session:
                return await reminder_repo.get(session, rid)  # type: ignore[return-value]

        # Pre-alert jobs (only those still in the future).
        for minutes in pre_alerts_minutes or []:
            run_at = when_dt - timedelta(minutes=minutes)
            if run_at <= now:
                continue
            schedule_at(
                scheduler,
                kind=ScheduleKind.reminder,
                row_id=rid,
                run_at=run_at,
                role="pre_alert",
            )

        # Deadline job (persist its id on the row).
        due_job_id = schedule_at(
            scheduler,
            kind=ScheduleKind.reminder,
            row_id=rid,
            run_at=when_dt,
            role="deadline",
        )
        async with self.registry.session() as session:
            await reminder_repo.set_job_id(session, rid, due_job_id)

        logger.info("reminder.created", reminder_id=rid, due_at=when_dt.isoformat())
        async with self.registry.session() as session:
            return await reminder_repo.get(session, rid)  # type: ignore[return-value]

    async def fire(self, reminder_id: int, role: str = "") -> None:
        """Fire a reminder: notify the owner with action buttons.

        A one-shot deadline marks the row ``fired``; a ``role="recurring"`` cron
        occurrence leaves the row ``pending`` so it keeps repeating. The buttons
        let the owner mark it done or snooze it without typing.
        """
        from app.bot.keyboards import KIND_REMINDER, item_actions

        async with self.registry.session() as session:
            reminder = await reminder_repo.get(session, reminder_id)
            if reminder is None:
                logger.warning("reminder.fire.missing", reminder_id=reminder_id)
                return
            if reminder.status in (ReminderStatus.cancelled, ReminderStatus.done):
                return
            title = reminder.title
            body = reminder.body
            due_at = reminder.due_at

        when_str = to_local_str(due_at, self.registry.settings.user_timezone)
        recurring = role == "recurring"

        if role == "pre_alert":
            text = f"⏰ Eslatma yaqinlashmoqda ({when_str}):\n{title}"
        elif recurring:
            text = f"🔁 Eslatma: {title}"
        else:
            text = f"⏰ Eslatma vaqti keldi:\n{title}"
        if body:
            text += f"\n{body}"

        notifier = self.registry.notification_service
        if notifier is not None:
            markup = item_actions(KIND_REMINDER, reminder_id, recurring=recurring)
            await notifier.notify_owner(text, reply_markup=markup)

        # A non-recurring deadline closes the reminder; pre-alerts and recurring
        # occurrences leave it pending so it can fire again.
        if role not in ("pre_alert", "recurring"):
            async with self.registry.session() as session:
                await reminder_repo.set_status(
                    session, reminder_id, ReminderStatus.fired
                )

    async def mark_done(self, reminder_id: int) -> bool:
        """Mark a reminder done and cancel its scheduled job. ``True`` if it existed."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            reminder = await reminder_repo.get(session, reminder_id)
            if reminder is None:
                return False
            job_id = reminder.apscheduler_job_id
            await reminder_repo.set_status(session, reminder_id, ReminderStatus.done)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("reminder.done", reminder_id=reminder_id)
        return True

    async def snooze(self, reminder_id: int, target_dt: datetime) -> bool:
        """Move a reminder to ``target_dt`` and reschedule its deadline job."""
        from app.scheduler.jobs import cancel_job, schedule_at

        async with self.registry.session() as session:
            reminder = await reminder_repo.get(session, reminder_id)
            if reminder is None:
                return False
            old_job = reminder.apscheduler_job_id
            reminder.due_at = target_dt
            reminder.status = ReminderStatus.pending
            await session.flush()
        if old_job:
            cancel_job(self.registry.scheduler, old_job)
        job_id = schedule_at(
            self.registry.scheduler,
            kind=ScheduleKind.reminder,
            row_id=reminder_id,
            run_at=target_dt,
            role="deadline",
        )
        async with self.registry.session() as session:
            await reminder_repo.set_job_id(session, reminder_id, job_id)
        logger.info("reminder.snoozed", reminder_id=reminder_id, to=target_dt.isoformat())
        return True

    async def cancel(self, reminder_id: int) -> None:
        """Cancel a reminder and remove its scheduled deadline job."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            reminder = await reminder_repo.get(session, reminder_id)
            if reminder is None:
                return
            job_id = reminder.apscheduler_job_id
            await reminder_repo.set_status(
                session, reminder_id, ReminderStatus.cancelled
            )
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("reminder.cancelled", reminder_id=reminder_id)
