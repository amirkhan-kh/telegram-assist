"""BriefingService — the morning plan (07:00) and the evening day-end review.

The morning briefing is one beautiful HTML post: today's meetings, today's tasks,
important calls, yesterday's unfinished items, upcoming important dates, and the
day's top-3 priorities (a zero-cost heuristic, no LLM call). The evening review
summarises what got done and turns each leftover into a card with «✅ Bajarildi»
and «➡️ Ertaga» buttons so it can be closed or rolled to tomorrow in one tap.

Both are delivered to the owner through :class:`NotificationService`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.db.base import utcnow
from app.db.models.enums import TaskKind
from app.logging_conf import get_logger
from app.repositories import (
    meeting_repo,
    person_repo,
    reminder_repo,
    setting_repo,
    task_repo,
)
from app.services._timeutil import as_utc, to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

_WEEKDAYS_UZ = (
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
)

# Heuristic markers that flag a task/reminder as an important call.
_CALL_MARKERS = ("qo'ng'iroq", "qongiroq", "qo‘ng‘iroq", "call", "telefon", "aloqa")

# Settings key for the "morning plan must be confirmed first" gate. While set to
# a non-empty value (today's date) the bot blocks all owner actions until the
# owner taps «✅ Tasdiqlash» on the morning plan.
_MORNING_GATE_KEY = "morning_ack_pending"


@dataclass
class _Item:
    """A unified agenda item across reminders, promises, tasks and meetings."""

    kind: str  # 'rem' | 'prm' | 'tsk' | 'mtg'
    item_id: int
    title: str
    when: datetime | None
    recurring: bool = False
    note: str = ""  # assignee name / meet link, for display


class BriefingService:
    """Builds and delivers the morning plan and the evening review."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.registry.settings.user_timezone)

    # ── public entry points ─────────────────────────────────────────────────
    async def run_morning(self) -> str | None:
        """Build and deliver the morning plan with a confirmation gate.

        Delivering the plan raises the gate (``_MORNING_GATE_KEY``); the owner must
        tap «✅ Tasdiqlash» before the bot will act on anything else that day.
        """
        from app.bot.keyboards import morning_confirm_button

        now = utcnow()
        owner_id, today, overdue, events = await self._collect(now)
        if owner_id is None:
            return None
        emails = await self._fetch_emails()
        text = self._format_morning(now, today, overdue, events, emails)
        await self.set_morning_pending(now)
        notifier = self.registry.notification_service
        if notifier is not None:
            await notifier.notify_owner(
                text, parse_mode="HTML", reply_markup=morning_confirm_button()
            )
        logger.info("briefing.morning.delivered", today=len(today), overdue=len(overdue))
        return text

    # ── morning confirmation gate ───────────────────────────────────────────
    async def set_morning_pending(self, now: datetime) -> None:
        """Raise the gate, stamped with today's local date."""
        day = now.astimezone(self._tz).date().isoformat()
        async with self.registry.session() as session:
            await setting_repo.set_value(session, _MORNING_GATE_KEY, day)

    async def clear_morning_pending(self) -> None:
        """Lower the gate (owner confirmed the plan)."""
        async with self.registry.session() as session:
            await setting_repo.set_value(session, _MORNING_GATE_KEY, "")

    async def is_morning_pending(self) -> bool:
        """True when a morning plan is awaiting the owner's confirmation."""
        async with self.registry.session() as session:
            value = await setting_repo.get_value(session, _MORNING_GATE_KEY)
        return bool(value)

    async def run_evening(self) -> str | None:
        """Deliver the interactive end-of-day review (checklist of today's items)."""
        now = utcnow()
        text, kb = await self.eod_message(now)
        if text is None:
            return None
        notifier = self.registry.notification_service
        if notifier is not None:
            await notifier.notify_owner(text, parse_mode="HTML", reply_markup=kb)
        logger.info("briefing.evening.delivered")
        return text

    # ── end-of-day review ("Kun yakuni") ────────────────────────────────────
    async def collect_eod(self, now: datetime) -> list[tuple[str, int, str]]:
        """Return today's + overdue still-pending items as ``(kind, id, title)``."""
        owner_id, today, overdue, _events = await self._collect(now)
        if owner_id is None:
            return []
        return [
            (it.kind, it.item_id, it.title)
            for it in (today + overdue)
            if it.kind in ("rem", "prm", "tsk")
        ]

    async def eod_message(
        self, now: datetime
    ) -> tuple[str | None, object | None]:
        """Build the end-of-day checklist message + keyboard (recomputed live)."""
        owner_id, today, overdue, _events = await self._collect(now)
        if owner_id is None:
            return None, None
        done_count = await self._count_done_today(owner_id, now)
        leftovers = [
            (it.kind, it.item_id, it.title)
            for it in (today + overdue)
            if it.kind in ("rem", "prm", "tsk")
        ]

        local = now.astimezone(self._tz)
        header = f"🌙 <b>Kun yakuni</b> · <i>{local.strftime('%d.%m.%Y')}</i>"
        if not leftovers:
            text = (
                f"{header}\n\n✅ Bugun bajarilgan: {done_count} ta\n"
                "🎉 Barakalla — bugungi hammasi bajarildi!"
            )
            return text, None

        from app.bot.keyboards import eod_checklist

        text = (
            f"{header}\n\n"
            f"✅ Bugun bajarilgan: {done_count} ta\n"
            f"⌛ Qolgan: {len(leftovers)} ta\n\n"
            "Bugun <b>bajarganlaringizni</b> belgilang 👇"
        )
        return text, eod_checklist(leftovers)

    # ── data collection ──────────────────────────────────────────────────────
    async def _collect(
        self, now: datetime
    ) -> tuple[int | None, list[_Item], list[_Item], list]:
        """Gather today's items, overdue items and upcoming important dates."""
        tz = self._tz
        today_local = now.astimezone(tz).date()

        async with self.registry.session() as session:
            owner = await person_repo.get_owner(session)
            if owner is None:
                return None, [], [], []
            owner_id = owner.id
            reminders = await reminder_repo.list_active(session, owner_id)
            promises = await task_repo.list_open(
                session, owner_id=owner_id, kind=TaskKind.self_promise
            )
            delegated_rows = await task_repo.list_open(session, kind=TaskKind.delegated)
            delegated: list[_Item] = []
            for task in delegated_rows:
                if task.created_by_id != owner_id:
                    continue
                assignee = await person_repo.get_by_id(session, task.owner_id)
                name = getattr(assignee, "display_name", "kishi")
                delegated.append(
                    _Item("tsk", task.id, task.title, task.due_at, note=name)
                )
            meetings = await meeting_repo.list_upcoming(session, owner_id)

        items: list[_Item] = []
        for r in reminders:
            rec = bool(r.recurrence)
            # Transient reminders whose time has already passed drop out of the
            # plan entirely (they are not "overdue" work).
            if not rec and r.due_at is not None and as_utc(r.due_at) < as_utc(now):
                continue
            items.append(_Item("rem", r.id, r.title, r.due_at, recurring=rec))
        for p in promises:
            items.append(_Item("prm", p.id, p.title, p.due_at))
        items.extend(delegated)
        for m in meetings:
            items.append(_Item("mtg", m.id, m.title, m.start_at, note=m.meet_link or ""))

        today: list[_Item] = []
        overdue: list[_Item] = []
        for it in items:
            if it.when is None:
                today.append(it)  # undated items surface in "today"
                continue
            d = as_utc(it.when).astimezone(tz).date()
            if d == today_local:
                today.append(it)
            elif d < today_local and not it.recurring:
                overdue.append(it)
        today.sort(key=lambda i: as_utc(i.when) if i.when else as_utc(now))
        overdue.sort(key=lambda i: as_utc(i.when) if i.when else as_utc(now))

        events = []
        if self.registry.event_service is not None:
            events = await self.registry.event_service.list_upcoming(
                owner_id, days=self.registry.settings.important_date_lookahead_days
            )
        return owner_id, today, overdue, events

    async def _count_done_today(self, owner_id: int, now: datetime) -> int:
        """Count reminders + self-promises the owner completed since midnight."""
        tz = self._tz
        midnight_local = datetime.combine(
            now.astimezone(tz).date(), datetime.min.time(), tzinfo=tz
        )
        since = midnight_local.astimezone(ZoneInfo("UTC"))
        async with self.registry.session() as session:
            rem = await reminder_repo.count_done_since(session, owner_id, since)
            tsk = await task_repo.count_done_since(session, owner_id, since)
        return rem + tsk

    # ── formatting ────────────────────────────────────────────────────────────
    async def _fetch_emails(self) -> list:
        """Best-effort fetch of unread emails for the plan (empty on any failure)."""
        gmail = self.registry.gmail_service
        if gmail is None or not gmail.available():
            return []
        try:
            return await gmail.list_unread(
                max_results=self.registry.settings.gmail_max_results or 5
            )
        except Exception as exc:  # noqa: BLE001 - email is optional in the plan
            logger.warning("briefing.gmail.failed", error=str(exc)[:120])
            return []

    def _format_morning(
        self,
        now: datetime,
        today: list[_Item],
        overdue: list[_Item],
        events: list,
        emails: list | None = None,
    ) -> str:
        local = now.astimezone(self._tz)
        header = (
            f"🌅 <b>Xayrli tong!</b>\n"
            f"<i>{_WEEKDAYS_UZ[local.weekday()]}, {local.strftime('%d.%m.%Y')}</i>"
        )

        meetings = [it for it in today if it.kind == "mtg"]
        tasks = [it for it in today if it.kind != "mtg"]
        calls = [it for it in tasks if self._is_call(it.title)]

        blocks = [header]

        if meetings:
            blocks.append(
                self._block("📅", "Bugungi uchrashuvlar", [self._line(it) for it in meetings])
            )
        if tasks:
            blocks.append(
                self._block("⏰", "Bugungi vazifalar", [self._line(it) for it in tasks])
            )
        if calls:
            blocks.append(
                self._block("📞", "Muhim qo'ng'iroqlar", [self._line(it) for it in calls])
            )
        if overdue:
            blocks.append(
                self._block(
                    "⚠️", "Kechagi bajarilmaganlar", [self._line(it) for it in overdue]
                )
            )
        if events:
            blocks.append(
                self._block("🎂", "Yaqin muhim sanalar", self._event_lines(events, now))
            )
        if emails:
            blocks.append(self._block("📧", "Muhim xatlar", self._email_lines(emails)))

        priorities = self._priorities(today, overdue)
        if priorities:
            lines = [
                f"{i}. {html.escape(p, quote=False)}"
                for i, p in enumerate(priorities, start=1)
            ]
            blocks.append("⭐ <b>Bugungi 3 ta prioritet</b>\n" + "\n".join(lines))

        if not meetings and not tasks and not overdue and not events and not emails:
            blocks.append(
                "✨ Bugun reja bo'sh. Dam oling yoki yangi reja qo'shing — "
                "menga shunchaki yozing."
            )
        return "\n\n".join(blocks)

    # ── small helpers ─────────────────────────────────────────────────────────
    def _priorities(self, today: list[_Item], overdue: list[_Item]) -> list[str]:
        """Pick the day's top-3 priorities: overdue first, then meetings, then due."""
        ranked: list[_Item] = []
        ranked.extend(overdue)  # already oldest-first
        ranked.extend(it for it in today if it.kind == "mtg")
        ranked.extend(it for it in today if it.kind != "mtg")
        seen: set[tuple[str, int]] = set()
        out: list[str] = []
        for it in ranked:
            key = (it.kind, it.item_id)
            if key in seen:
                continue
            seen.add(key)
            label = it.title
            if it.note and it.kind == "tsk":
                label = f"{it.note}: {label}"
            when = self._local(it.when)
            out.append(f"{label} ({when})" if when else label)
            if len(out) >= 3:
                break
        return out

    @staticmethod
    def _is_call(title: str) -> bool:
        low = title.lower()
        return any(marker in low for marker in _CALL_MARKERS)

    def _line(self, it: _Item) -> str:
        title = html.escape(it.title, quote=False)
        when = self._local(it.when)
        prefix = ""
        if it.kind == "tsk" and it.note:
            prefix = f"<b>{html.escape(it.note, quote=False)}</b> — "
        if it.kind == "rem" and it.recurring:
            prefix = "🔁 "
        suffix = f" — {when}" if when else ""
        if it.kind == "mtg" and it.note:
            link = html.escape(it.note, quote=True)
            suffix += f' · <a href="{link}">Meet</a>'
        return f"• {prefix}{title}{suffix}"

    def _event_lines(self, events: list, now: datetime) -> list[str]:
        from app.services.event_service import category_icon

        lines = []
        for event in events:
            icon = category_icon(event.category)
            date_str = event.event_date.strftime("%d.%m")
            left = ""
            if event.next_fire_at is not None:
                days_left = (as_utc(event.next_fire_at) - as_utc(now)).days
                if days_left <= 0:
                    left = " — <b>bugun!</b>"
                else:
                    left = f" — {days_left} kun"
            title = html.escape(event.title, quote=False)
            lines.append(f"{icon} {title} ({date_str}){left}")
        return lines

    @staticmethod
    def _email_lines(emails: list) -> list[str]:
        lines = []
        for e in emails:
            mark = "⭐ " if getattr(e, "important", False) else ""
            sender = html.escape(getattr(e, "sender", ""), quote=False)
            subject = html.escape(getattr(e, "subject", ""), quote=False)
            lines.append(f"{mark}<b>{sender}</b> — {subject}")
        return lines

    @staticmethod
    def _block(emoji: str, label: str, items: list[str]) -> str:
        body = "\n".join(items)
        return f"{emoji} <b>{label} ({len(items)})</b>\n<blockquote>{body}</blockquote>"

    def _local(self, dt: datetime | None) -> str:
        return to_local_str(dt, self.registry.settings.user_timezone)
