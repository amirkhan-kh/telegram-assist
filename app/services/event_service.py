"""EventService — important dates & birthdays with pre-alerts.

Creating an event computes its next occurrence, persists an :class:`Event`, and
schedules ``important_date`` jobs: one per "days before" value plus one on the day
itself. When the day-of alert fires, a *yearly* event is re-armed for next year;
a one-off event is marked done.

Scheduling goes through ``app.scheduler.jobs`` (imported lazily).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.db.base import utcnow
from app.db.models.enums import EventCategory, EventStatus, ScheduleKind
from app.db.models.event import Event
from app.logging_conf import get_logger
from app.repositories import event_repo
from app.services._timeutil import to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

# Local hour the day-of and pre-alerts fire on.
_ALERT_HOUR = 9

_CATEGORY_ICON = {
    EventCategory.birthday: "🎂",
    EventCategory.document: "📄",
    EventCategory.payment: "💳",
    EventCategory.travel: "✈️",
    EventCategory.health: "🩺",
    EventCategory.other: "📌",
}


# Structured personal-data dates: what base date the owner enters and how the
# tracked renewal/expiry date is derived from it.
_PERSONAL_SPECS: dict[str, dict] = {
    "passport": {
        "title": "🪪 Pasportni yangilash",
        "category": EventCategory.document,
        "years": 10,        # adult biometric passport validity
        "yearly": False,    # one-off; the owner re-enters after renewing
        "remind": (30, 7, 1),
    },
    "inspection": {
        "title": "🚗 Mashina texnik ko'rigi",
        "category": EventCategory.other,
        "years": 0,         # owner enters the expiry date directly (no offset)
        "yearly": True,     # annual obligation -> auto re-arms each year
        "remind": (14, 3, 1),
    },
    "insurance": {
        "title": "🛡 Mashina sug'urtasi muddati",
        "category": EventCategory.document,
        "years": 0,         # owner enters the expiry date directly (no offset)
        "yearly": True,
        "remind": (14, 3, 1),
    },
}


# Photographed-document expiries: the title shown and the pre-alert lead times
# the owner asked for (one week, three days, one day before the date).
_DOC_TITLES = {
    "passport": "🪪 Pasport muddati",
    "inspection": "🚗 Texnik ko'rik muddati",
    "insurance": "🛡 Sug'urta muddati",
}
_DOC_REMIND = (7, 3, 1)


def category_icon(category: EventCategory | str) -> str:
    """Return the emoji for an event category (accepts the enum or its value)."""
    if isinstance(category, str):
        try:
            category = EventCategory(category)
        except ValueError:
            return "📌"
    return _CATEGORY_ICON.get(category, "📌")


class EventService:
    """Important dates and birthdays with day-before reminders."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.registry.settings.user_timezone)

    async def add_event(
        self,
        *,
        owner_id: int,
        title: str,
        category: EventCategory,
        month: int,
        day: int,
        year: int | None = None,
        yearly: bool = True,
        remind_days_before: list[int] | None = None,
    ) -> Event:
        """Create an important date and schedule its pre-alerts."""
        now_local = utcnow().astimezone(self._tz)
        occurrence = self._next_occurrence(month, day, year, yearly, now_local)
        days_before = self._clean_days(remind_days_before)
        fire_at = self._occurrence_fire_dt(occurrence)

        async with self.registry.session() as session:
            event = await event_repo.create(
                session,
                owner_id=owner_id,
                title=title,
                category=category,
                event_date=occurrence,
                yearly=yearly,
                remind_days_before=days_before,
                next_fire_at=fire_at,
                status=EventStatus.active,
                job_ids=[],
            )
            eid = event.id

        await self._schedule_alerts(eid, occurrence, days_before)
        # Best-effort: also surface this date on the owner's Google Calendar so it
        # shows up under the Calendar view (yearly dates recur via an RRULE).
        await self._sync_to_calendar(title, occurrence, yearly)
        logger.info("event.added", event_id=eid, date=occurrence.isoformat())
        async with self.registry.session() as session:
            return await event_repo.get(session, eid)  # type: ignore[return-value]

    async def _sync_to_calendar(
        self, title: str, occurrence: date, yearly: bool
    ) -> None:
        """Mirror an important date onto Google Calendar as an all-day event."""
        from app.integrations.google.calendar import add_calendar_event

        start = datetime.combine(occurrence, time(0, 0), tzinfo=self._tz)
        recurrence = ["RRULE:FREQ=YEARLY"] if yearly else None
        await add_calendar_event(
            getattr(self.registry, "calendar_service", None),
            title=title,
            start=start,
            all_day=True,
            recurrence=recurrence,
        )

    async def add_personal_date(
        self, *, owner_id: int, kind: str, base_date: date
    ) -> Event:
        """Create a tracked personal-data event from a base date.

        ``kind`` is ``passport`` (issue date -> +10y renewal, one-off), or
        ``inspection``/``insurance`` (the owner enters the expiry date directly
        -> +0y, yearly). The renewal/expiry date is computed and scheduled with
        the per-kind pre-alerts.
        """
        spec = _PERSONAL_SPECS[kind]
        target = self._safe_date(
            base_date.year + spec["years"], base_date.month, base_date.day
        )
        return await self.add_event(
            owner_id=owner_id,
            title=spec["title"],
            category=spec["category"],
            month=target.month,
            day=target.day,
            year=target.year,
            yearly=spec["yearly"],
            remind_days_before=list(spec["remind"]),
        )

    async def add_document_event(
        self, *, owner_id: int, kind: str, expiry: date
    ) -> Event:
        """Track a photographed document's expiry date with 7/3/1-day pre-alerts.

        ``expiry`` is the date read from the document image (or typed by the owner
        as a fallback); it is used directly — the document flow never adds an
        offset. ``yearly=False``: the owner re-photographs after renewing.
        """
        title = _DOC_TITLES.get(kind, "📄 Hujjat muddati")
        return await self.add_event(
            owner_id=owner_id,
            title=title,
            category=EventCategory.document,
            month=expiry.month,
            day=expiry.day,
            year=expiry.year,
            yearly=False,
            remind_days_before=list(_DOC_REMIND),
        )

    async def fire(self, event_id: int, role: str = "") -> None:
        """Alert the owner about an upcoming/today important date."""
        async with self.registry.session() as session:
            event = await event_repo.get(session, event_id)
            if event is None or event.status != EventStatus.active:
                return
            title = event.title
            category = event.category
            event_date = event.event_date
            yearly = event.yearly

        icon = category_icon(category)
        date_str = event_date.strftime("%d.%m")
        if role.startswith("pre:"):
            days = role.split(":", 1)[1]
            text = f"{icon} {days} kundan keyin ({date_str}): {title}"
        else:
            text = f"{icon} Bugun: {title}"

        notifier = self.registry.notification_service
        if notifier is not None:
            await notifier.notify_owner(text)

        # On the day itself, roll a yearly event to next year or close a one-off.
        if not role.startswith("pre:"):
            if yearly:
                await self._rearm_next_year(event_id)
            else:
                async with self.registry.session() as session:
                    await event_repo.set_status(session, event_id, EventStatus.done)

    async def list_active(self, owner_id: int) -> list[Event]:
        """Return the owner's active important dates (soonest first)."""
        async with self.registry.session() as session:
            return await event_repo.list_active(session, owner_id)

    async def list_upcoming(self, owner_id: int, *, days: int) -> list[Event]:
        """Return active events whose next occurrence is within ``days`` days."""
        before = utcnow() + timedelta(days=days)
        async with self.registry.session() as session:
            return await event_repo.list_upcoming(session, owner_id, before=before)

    async def cancel(self, event_id: int) -> bool:
        """Cancel an event and drop all its scheduled alert jobs."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            event = await event_repo.get(session, event_id)
            if event is None:
                return False
            job_ids = list(event.job_ids or [])
            await event_repo.set_status(session, event_id, EventStatus.cancelled)
        for job_id in job_ids:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("event.cancelled", event_id=event_id)
        return True

    # ── scheduling helpers ────────────────────────────────────────────────
    async def _schedule_alerts(
        self, event_id: int, occurrence: date, days_before: list[int]
    ) -> None:
        """Schedule one ``important_date`` job per pre-alert + the day itself."""
        from app.scheduler.jobs import schedule_at

        now = utcnow()
        job_ids: list[str] = []
        # 0 == the day itself; always included so a yearly event re-arms.
        for d in sorted(set(days_before) | {0}, reverse=True):
            fire_at = self._occurrence_fire_dt(occurrence) - timedelta(days=d)
            if fire_at <= now:
                continue
            role = "day" if d == 0 else f"pre:{d}"
            job_id = schedule_at(
                self.registry.scheduler,
                kind=ScheduleKind.important_date,
                row_id=event_id,
                run_at=fire_at,
                role=role,
            )
            job_ids.append(job_id)

        async with self.registry.session() as session:
            event = await event_repo.get(session, event_id)
            if event is not None:
                event.job_ids = job_ids
                await session.flush()

    async def _rearm_next_year(self, event_id: int) -> None:
        """Roll a yearly event forward to next year and reschedule its alerts."""
        async with self.registry.session() as session:
            event = await event_repo.get(session, event_id)
            if event is None:
                return
            old = event.event_date
            next_date = self._safe_date(old.year + 1, old.month, old.day)
            event.event_date = next_date
            event.next_fire_at = self._occurrence_fire_dt(next_date)
            days_before = list(event.remind_days_before or [])
            await session.flush()
        await self._schedule_alerts(event_id, next_date, days_before)
        logger.info("event.rearmed", event_id=event_id, date=next_date.isoformat())

    def _next_occurrence(
        self, month: int, day: int, year: int | None, yearly: bool, now_local: datetime
    ) -> date:
        """Compute the next occurrence date for an event.

        When an explicit ``year`` is given (e.g. a computed passport-expiry date),
        that exact date is honoured. Otherwise a yearly event lands on this year's
        month/day if still ahead, else next year's.
        """
        if year is not None:
            return self._safe_date(year, month, day)
        candidate = self._safe_date(now_local.year, month, day)
        if candidate < now_local.date():
            candidate = self._safe_date(now_local.year + 1, month, day)
        return candidate

    def _occurrence_fire_dt(self, occurrence: date) -> datetime:
        """The day-of alert instant (occurrence at the local alert hour), in UTC."""
        local = datetime.combine(occurrence, time(_ALERT_HOUR, 0), tzinfo=self._tz)
        return local.astimezone(UTC)

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> date:
        """Build a date, clamping an out-of-range day (e.g. Feb 29) to month end."""
        for d in (day, 28):
            try:
                return date(year, month, min(d, 31))
            except ValueError:
                continue
        return date(year, month, 28)

    @staticmethod
    def _clean_days(values: list[int] | None) -> list[int]:
        """Sanitize pre-alert offsets to a sorted, de-duplicated 1..365 list."""
        cleaned = sorted({int(v) for v in (values or []) if 1 <= int(v) <= 365})
        return cleaned or [1]

    def _local_str(self, dt: datetime | None) -> str:
        return to_local_str(dt, self.registry.settings.user_timezone)
