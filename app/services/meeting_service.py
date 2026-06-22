"""MeetingService — meetings with day-before / hour-before owner reminders and an
at-start Meet-link delivery.

Creating a meeting persists the Meeting plus three MeetingAlert rows
(1 day / 1 hour / 0). The day- and hour-before alerts schedule ``meeting_alert``
jobs that ping the owner; the 0 alert schedules a ``meeting_link`` job that
delivers the Meet link to the target (redirected to the owner in test mode).
Short-notice meetings simply skip any alert whose fire time has already passed.

Scheduling goes through ``app.scheduler.jobs`` (imported lazily).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.enums import (
    MeetingAlertKind,
    NotifyTargetKind,
    ScheduleKind,
    SendMode,
)
from app.db.models.meeting import Meeting
from app.logging_conf import get_logger
from app.repositories import meeting_repo, person_repo
from app.services._timeutil import to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

# Owner reminders before a meeting: one day and one hour before. Any offset
# whose fire time is already past (short-notice meetings) is silently skipped.
_REMINDER_OFFSETS = (1440, 60)


class MeetingService:
    """Meetings, owner reminders, and at-start Meet-link delivery."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def create_meeting(
        self,
        *,
        owner_id: int,
        title: str,
        start_at: datetime,
        end_at: datetime,
        meet_link: str | None = None,
        notify_target_kind: NotifyTargetKind | None = None,
        notify_target_ref: str | None = None,
        gcal_event_id: str | None = None,
    ) -> Meeting:
        """Create a meeting + its 30/15/0 alert rows and schedule their jobs."""
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            meeting = await meeting_repo.create(
                session,
                owner_id=owner_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                meet_link=meet_link,
                gcal_event_id=gcal_event_id,
                notify_target_kind=notify_target_kind,
                notify_target_ref=notify_target_ref,
            )
            mid = meeting.id

        scheduler = self.registry.scheduler
        now = utcnow()

        # 30 and 15 minute owner reminders.
        for offset in _REMINDER_OFFSETS:
            fire_at = start_at - timedelta(minutes=offset)
            async with self.registry.session() as session:
                alert = await meeting_repo.add_alert(
                    session,
                    meeting_id=mid,
                    offset_minutes=offset,
                    fire_at=fire_at,
                    kind=MeetingAlertKind.reminder,
                )
                aid = alert.id
            if fire_at <= now:
                continue
            job_id = schedule_at(
                scheduler,
                kind=ScheduleKind.meeting_alert,
                row_id=aid,
                run_at=fire_at,
                role=f"offset:{offset}",
            )
            async with self.registry.session() as session:
                await meeting_repo.set_alert_job_id(session, aid, job_id)

        # At-start Meet-link delivery (offset 0).
        async with self.registry.session() as session:
            link_alert = await meeting_repo.add_alert(
                session,
                meeting_id=mid,
                offset_minutes=0,
                fire_at=start_at,
                kind=MeetingAlertKind.send_link,
            )
            link_aid = link_alert.id
        if start_at > now:
            job_id = schedule_at(
                scheduler,
                kind=ScheduleKind.meeting_link,
                row_id=link_aid,
                run_at=start_at,
            )
            async with self.registry.session() as session:
                await meeting_repo.set_alert_job_id(session, link_aid, job_id)

        logger.info("meeting.created", meeting_id=mid, start_at=start_at.isoformat())
        async with self.registry.session() as session:
            return await meeting_repo.get(session, mid)  # type: ignore[return-value]

    async def fire_alert(self, alert_id: int, role: str = "") -> None:
        """Remind the owner AND the designated contact a meeting is approaching.

        The owner gets a control-bot notice; the meeting's notify target (if any)
        gets a polite written reminder via the userbot (redirected to the owner
        in test mode). Both fire at the 30- and 15-minute marks.
        """
        async with self.registry.session() as session:
            alert = await meeting_repo.get_alert(session, alert_id)
            if alert is None or alert.fired:
                return
            meeting = await meeting_repo.get(session, alert.meeting_id)
            if meeting is None:
                return
            title = meeting.title
            start_at = meeting.start_at
            offset = alert.offset_minutes
            target_kind = meeting.notify_target_kind
            target_ref = meeting.notify_target_ref
            await meeting_repo.mark_alert_fired(session, alert_id)

            chat_id: int | None = None
            target_name = "ishtirokchi"
            if target_kind is not None and target_ref is not None:
                chat_id, target_name = await self._resolve_target(
                    session, target_kind, target_ref
                )

        when_str = self._local_str(start_at)
        notifier = self.registry.notification_service
        offset_label = self._offset_label(offset)

        # 1) Owner notice via the control bot (with acknowledge / move buttons).
        if notifier is not None:
            from app.bot.keyboards import meeting_actions

            await notifier.notify_owner(
                f"📅 Uchrashuv {offset_label}dan keyin ({when_str}):\n{title}",
                reply_markup=meeting_actions(meeting.id),
            )

        # 2) Written reminder to the designated contact (skip if it's the owner).
        settings = self.registry.settings
        if chat_id is None or chat_id == settings.owner_chat_id:
            return
        body = (
            f"Eslatma: «{title}» uchrashuvigacha {offset_label} qoldi "
            f"(boshlanishi: {when_str})."
        )
        if settings.test_mode:
            if notifier is not None:
                await notifier.notify_owner(f"[TEST -> {target_name}]\n{body}")
            return
        sender = self.registry.sender
        if sender is not None:
            await sender.send(chat_id, body, SendMode.text)
            logger.info(
                "meeting.alert.target_reminded", offset=offset, chat=str(chat_id)
            )

    async def deliver_link(self, alert_id: int) -> None:
        """At meeting start: ping the owner that it's starting, then deliver the
        Meet link to a separate target (person/group). In test mode the target
        copy is redirected to the owner."""
        async with self.registry.session() as session:
            alert = await meeting_repo.get_alert(session, alert_id)
            if alert is None or alert.fired:
                return
            meeting = await meeting_repo.get(session, alert.meeting_id)
            if meeting is None:
                return
            title = meeting.title
            meet_link = meeting.meet_link
            target_kind = meeting.notify_target_kind
            target_ref = meeting.notify_target_ref
            await meeting_repo.mark_alert_fired(session, alert_id)

            chat_id: int | None = None
            target_name = "ishtirokchi"
            if target_kind is not None and target_ref is not None:
                chat_id, target_name = await self._resolve_target(
                    session, target_kind, target_ref
                )

        settings = self.registry.settings
        owner_chat_id = settings.owner_chat_id
        notifier = self.registry.notification_service

        # 1) Always tell the owner the meeting is starting now (with link if any).
        start_text = f"Uchrashuv boshlanmoqda: {title}"
        if meet_link:
            start_text += f"\nMeet: {meet_link}"
        if notifier is not None:
            await notifier.notify_owner(start_text)

        # 2) Tell the target the meeting has started + the Meet link (written).
        if not meet_link or chat_id is None or chat_id == owner_chat_id:
            return
        # Plain chat message — just the notice + the link (no title/name).
        body = f"✅ Meeting boshlandi, havolaga kiring:\n{meet_link}"
        if settings.test_mode:
            if notifier is not None:
                await notifier.notify_owner(f"[TEST -> {target_name}]\n{body}")
            return
        sender = self.registry.sender
        if sender is not None:
            await sender.send(chat_id, body, SendMode.text)
            logger.info("meeting.link.delivered", alert_id=alert_id)

    async def move(self, meeting_id: int, target_dt: datetime) -> bool:
        """Move a meeting to ``target_dt`` (keeping its duration) and reschedule alerts.

        Existing alert jobs are cancelled and each alert row is re-armed at its
        offset relative to the new start. Returns ``True`` if the meeting existed.
        """
        from app.scheduler.jobs import cancel_job, schedule_at

        scheduler = self.registry.scheduler
        now = utcnow()
        async with self.registry.session() as session:
            meeting = await meeting_repo.get(session, meeting_id)
            if meeting is None:
                return False
            duration = meeting.end_at - meeting.start_at
            meeting.start_at = target_dt
            meeting.end_at = target_dt + duration
            await session.flush()
            alerts = await meeting_repo.list_alerts(session, meeting_id)
            alert_rows = [
                (a.id, a.offset_minutes, a.kind, a.apscheduler_job_id) for a in alerts
            ]

        for _aid, _offset, _kind, old_job in alert_rows:
            if old_job:
                cancel_job(scheduler, old_job)

        for aid, offset, kind, _old in alert_rows:
            fire_at = target_dt - timedelta(minutes=offset)
            if fire_at <= now:
                continue
            schedule_kind = (
                ScheduleKind.meeting_link
                if kind == MeetingAlertKind.send_link
                else ScheduleKind.meeting_alert
            )
            role = "" if kind == MeetingAlertKind.send_link else f"offset:{offset}"
            job_id = schedule_at(
                scheduler, kind=schedule_kind, row_id=aid, run_at=fire_at, role=role
            )
            async with self.registry.session() as session:
                await meeting_repo.set_alert_job_id(session, aid, job_id)
                await meeting_repo.reset_alert_fired(session, aid)

        logger.info("meeting.moved", meeting_id=meeting_id, to=target_dt.isoformat())
        return True

    async def cancel(self, meeting_id: int) -> bool:
        """Cancel a meeting: mark it cancelled and drop all its alert jobs.

        Returns ``True`` if the meeting existed, ``False`` otherwise.
        """
        from app.db.models.enums import MeetingStatus
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            meeting = await meeting_repo.get(session, meeting_id)
            if meeting is None:
                return False
            alerts = await meeting_repo.list_alerts(session, meeting_id)
            job_ids = [a.apscheduler_job_id for a in alerts if a.apscheduler_job_id]
            await meeting_repo.set_status(
                session, meeting_id, MeetingStatus.cancelled
            )
        for job_id in job_ids:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("meeting.cancelled", meeting_id=meeting_id)
        return True

    # ── helpers ───────────────────────────────────────────────────────────
    async def _resolve_target(
        self, session: object, kind: NotifyTargetKind, ref: str
    ) -> tuple[int | None, str]:
        """Resolve a meeting notify target to a chat id + display name."""
        if kind == NotifyTargetKind.group:
            try:
                return int(ref), "guruh"
            except ValueError:
                return None, "guruh"
        # person: ref is a people.id
        try:
            pid = int(ref)
        except ValueError:
            return None, "ishtirokchi"
        person = await person_repo.get_by_id(session, pid)  # type: ignore[arg-type]
        if person is None:
            return None, "ishtirokchi"
        return person.telegram_user_id, person.display_name

    def _local_str(self, dt: datetime | None) -> str:
        return to_local_str(dt, self.registry.settings.user_timezone)

    @staticmethod
    def _offset_label(minutes: int) -> str:
        """Human label for an alert offset: 1440 -> '1 kun', 60 -> '1 soat'."""
        if minutes % 1440 == 0:
            return f"{minutes // 1440} kun"
        if minutes % 60 == 0:
            return f"{minutes // 60} soat"
        return f"{minutes} daqiqa"
