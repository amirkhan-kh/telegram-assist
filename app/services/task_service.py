"""TaskService — the user's own promises and delegated/dependent tasks.

self_promise: the OWNER promised to do something by ``deadline_dt``; we schedule
``promise_alert`` jobs (pre-alerts + deadline) addressed to the owner.

delegated: someone (the assignee) owes the OWNER a deliverable. We build an Uzbek
nudge addressed to the assignee, store it on ``task.payload``, schedule
``followup_owner`` pre-alerts for the owner, and (when ``auto_followup``)
``followup_assignee`` jobs at ``deadline + offset`` that actually nudge the
assignee via the userbot sender (redirected to the owner in test mode).

Scheduling goes through ``app.scheduler.jobs`` (imported lazily).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.enums import (
    ScheduleKind,
    SendMode,
    Source,
    TaskKind,
    TaskStatus,
)
from app.db.models.task import Task
from app.logging_conf import get_logger
from app.repositories import person_repo, task_repo
from app.services._timeutil import to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class TaskService:
    """Self-promises and delegated tasks with automated follow-ups."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    # ── self promise ──────────────────────────────────────────────────────
    async def create_self_promise(
        self,
        *,
        owner_id: int,
        what: str,
        deadline_dt: datetime,
        counterparty_id: int | None = None,
        pre_alerts_minutes: list[int] | None = None,
        source: Source = Source.nlu,
    ) -> Task:
        """Create a self-promise task and schedule its promise_alert jobs."""
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            task = await task_repo.create(
                session,
                owner_id=owner_id,
                created_by_id=owner_id,
                counterparty_id=counterparty_id,
                title=what,
                kind=TaskKind.self_promise,
                status=TaskStatus.open,
                due_at=deadline_dt,
                source=source,
            )
            tid = task.id

        scheduler = self.registry.scheduler
        now = utcnow()
        for minutes in pre_alerts_minutes or []:
            run_at = deadline_dt - timedelta(minutes=minutes)
            if run_at <= now:
                continue
            schedule_at(
                scheduler,
                kind=ScheduleKind.promise_alert,
                row_id=tid,
                run_at=run_at,
                role="pre_alert",
            )
        deadline_job_id = schedule_at(
            scheduler,
            kind=ScheduleKind.promise_alert,
            row_id=tid,
            run_at=deadline_dt,
            role="deadline",
        )
        async with self.registry.session() as session:
            await task_repo.set_job_id(session, tid, deadline_job_id)

        logger.info("task.self_promise.created", task_id=tid)
        async with self.registry.session() as session:
            return await task_repo.get(session, tid)  # type: ignore[return-value]

    # ── delegated task ────────────────────────────────────────────────────
    async def create_delegated(
        self,
        *,
        assignee_id: int,
        created_by_id: int,
        task: str,
        deadline_dt: datetime,
        pre_alert_owner_minutes: list[int] | None = None,
        followup_offsets_minutes: list[int] | None = None,
        auto_followup: bool = True,
        delivery: SendMode = SendMode.voice,
        counterparty_id: int | None = None,
        source: Source = Source.nlu,
    ) -> Task:
        """Create a delegated task, store the assignee nudge, schedule follow-ups."""
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            assignee = await person_repo.get_by_id(session, assignee_id)
            nudge = self._build_nudge(assignee, task, deadline_dt)
            row = await task_repo.create(
                session,
                owner_id=assignee_id,
                created_by_id=created_by_id,
                counterparty_id=counterparty_id,
                title=task,
                kind=TaskKind.delegated,
                status=TaskStatus.open,
                due_at=deadline_dt,
                payload=nudge,
                delivery=delivery,
                source=source,
            )
            tid = row.id

        scheduler = self.registry.scheduler
        now = utcnow()

        # Owner pre-alerts before the deadline (followup_owner).
        for minutes in pre_alert_owner_minutes or []:
            run_at = deadline_dt - timedelta(minutes=minutes)
            if run_at <= now:
                continue
            schedule_at(
                scheduler,
                kind=ScheduleKind.followup_owner,
                row_id=tid,
                run_at=run_at,
                role="pre_alert",
            )

        # Auto follow-ups that nudge the assignee around the deadline.
        last_job_id: str | None = None
        if auto_followup:
            for offset in followup_offsets_minutes or [-15, 0]:
                run_at = deadline_dt + timedelta(minutes=offset)
                if run_at <= now:
                    continue
                last_job_id = schedule_at(
                    scheduler,
                    kind=ScheduleKind.followup_assignee,
                    row_id=tid,
                    run_at=run_at,
                    role=f"offset:{offset}",
                )

        if last_job_id is not None:
            async with self.registry.session() as session:
                await task_repo.set_job_id(session, tid, last_job_id)

        logger.info("task.delegated.created", task_id=tid, assignee_id=assignee_id)
        async with self.registry.session() as session:
            return await task_repo.get(session, tid)  # type: ignore[return-value]

    def _build_nudge(
        self, assignee: object | None, task: str, deadline_dt: datetime
    ) -> str:
        """Build an Uzbek nudge addressed to the assignee with an honorific."""
        when_str = to_local_str(deadline_dt, self.registry.settings.user_timezone)
        name = getattr(assignee, "display_name", None) or "Hurmatli"
        honorific = getattr(assignee, "honorific", None)
        greeting = f"{name} {honorific}".strip() if honorific else name
        return (
            f"Assalomu alaykum, {greeting}. "
            f"Eslatib o'taman: {task}. "
            f"Iltimos, {when_str} gacha bajarib bersangiz. Rahmat!"
        )

    # ── firing handlers ───────────────────────────────────────────────────
    async def fire_promise_alert(self, task_id: int, role: str = "") -> None:
        """Notify the owner about their own promise (pre-alert or deadline)."""
        from app.bot.keyboards import KIND_PROMISE, item_actions

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None or task.status in (
                TaskStatus.done,
                TaskStatus.cancelled,
            ):
                return
            title = task.title
            due_at = task.due_at

        when_str = self._local_str(due_at)
        if role == "pre_alert":
            text = f"🤝 Va'dangiz yaqinlashmoqda ({when_str}):\n{title}"
        else:
            text = f"🤝 Va'da vaqti keldi:\n{title}"
        await self._notify_owner(text, reply_markup=item_actions(KIND_PROMISE, task_id))

    async def fire_followup_owner(self, task_id: int, role: str = "") -> None:
        """Alert the owner that a delegated task is approaching its deadline."""
        from app.bot.keyboards import KIND_TASK, item_actions

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None or task.status in (
                TaskStatus.done,
                TaskStatus.cancelled,
            ):
                return
            title = task.title
            due_at = task.due_at
            assignee = await person_repo.get_by_id(session, task.owner_id)
            assignee_name = getattr(assignee, "display_name", "kishi")

        when_str = self._local_str(due_at)
        text = (
            f"✅ Topshiriq nazorati: {assignee_name} — \"{title}\" "
            f"(muddat: {when_str}). Tez orada eslatib qo'yiladi."
        )
        await self._notify_owner(text, reply_markup=item_actions(KIND_TASK, task_id))

    async def fire_followup_assignee(self, task_id: int, role: str = "") -> None:
        """Nudge the assignee via the userbot (redirected to owner in test mode)."""
        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None or task.status in (
                TaskStatus.done,
                TaskStatus.cancelled,
            ):
                return
            payload = task.payload or task.title
            delivery = task.delivery
            assignee = await person_repo.get_by_id(session, task.owner_id)
            assignee_chat_id = getattr(assignee, "telegram_user_id", None)
            assignee_name = getattr(assignee, "display_name", "kishi")

        settings = self.registry.settings
        if settings.test_mode:
            text = f"[TEST -> {assignee_name}]\n{payload}"
            await self._notify_owner(text)
            return

        sender = self.registry.sender
        if sender is None or assignee_chat_id is None:
            logger.warning(
                "task.followup_assignee.no_target",
                task_id=task_id,
                has_sender=sender is not None,
                chat_id=assignee_chat_id,
            )
            await self._notify_owner(
                f"Topshiriqni yetkazib bo'lmadi ({assignee_name}):\n{payload}"
            )
            return
        await sender.send(assignee_chat_id, payload, delivery)
        logger.info("task.followup_assignee.sent", task_id=task_id)

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def mark_done(self, task_id: int) -> None:
        """Mark a task done and cancel any remaining scheduled job."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None:
                return
            job_id = task.apscheduler_job_id
            await task_repo.set_status(session, task_id, TaskStatus.done)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("task.done", task_id=task_id)

    async def cancel(self, task_id: int) -> None:
        """Cancel a task and remove its scheduled job."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None:
                return
            job_id = task.apscheduler_job_id
            await task_repo.set_status(session, task_id, TaskStatus.cancelled)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("task.cancelled", task_id=task_id)

    async def snooze(self, task_id: int, target_dt: datetime) -> bool:
        """Schedule a fresh owner re-nudge for a task at ``target_dt``.

        Unlike a reminder, snoozing a task does not move its real deadline — it
        just asks to be reminded again later. A self-promise re-nudges via a
        ``promise_alert``; a delegated task via a ``followup_owner`` alert.
        """
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None or task.status in (TaskStatus.done, TaskStatus.cancelled):
                return False
            kind = (
                ScheduleKind.promise_alert
                if task.kind == TaskKind.self_promise
                else ScheduleKind.followup_owner
            )
        schedule_at(
            self.registry.scheduler,
            kind=kind,
            row_id=task_id,
            run_at=target_dt,
            role="pre_alert",
        )
        logger.info("task.snoozed", task_id=task_id, to=target_dt.isoformat())
        return True

    async def reschedule(self, task_id: int, target_dt: datetime) -> bool:
        """Move a task's deadline to ``target_dt`` and reschedule its alert.

        Used by the end-of-day "move to tomorrow" flow. The task stays open with
        a new due date and a fresh deadline alert (promise vs delegated).
        """
        from app.scheduler.jobs import cancel_job, schedule_at

        async with self.registry.session() as session:
            task = await task_repo.get(session, task_id)
            if task is None or task.status == TaskStatus.cancelled:
                return False
            old_job = task.apscheduler_job_id
            task.due_at = target_dt
            task.status = TaskStatus.open
            kind = task.kind
            await session.flush()
        if old_job:
            cancel_job(self.registry.scheduler, old_job)
        sk = (
            ScheduleKind.promise_alert
            if kind == TaskKind.self_promise
            else ScheduleKind.followup_owner
        )
        job_id = schedule_at(
            self.registry.scheduler,
            kind=sk,
            row_id=task_id,
            run_at=target_dt,
            role="deadline",
        )
        async with self.registry.session() as session:
            await task_repo.set_job_id(session, task_id, job_id)
        logger.info("task.rescheduled", task_id=task_id, to=target_dt.isoformat())
        return True

    # ── helpers ───────────────────────────────────────────────────────────
    def _local_str(self, dt: datetime | None) -> str:
        return to_local_str(dt, self.registry.settings.user_timezone)

    async def _notify_owner(self, text: str, *, reply_markup: object | None = None) -> None:
        notifier = self.registry.notification_service
        if notifier is not None:
            await notifier.notify_owner(text, reply_markup=reply_markup)
        else:  # pragma: no cover - notifier always wired in runtime
            logger.warning("task.notify.no_notifier", text=text)
