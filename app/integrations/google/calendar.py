"""Google Calendar service (Milestone 3).

A thin async wrapper over the Calendar v3 API. The ``googleapiclient`` discovery
client is built lazily and every blocking call runs through ``asyncio.to_thread``
so the event loop is never blocked. When no OAuth credentials are configured the
service reports :meth:`available` ``False`` and callers degrade gracefully.

Three capabilities back the meeting features:
  * :meth:`get_busy` — busy intervals from a free/busy query;
  * :meth:`find_free_slots` — free gaps within the owner's working hours;
  * :meth:`create_event_with_meet` — an event with an attached Google Meet link.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class CalEvent:
    """A single Google Calendar event, normalised for display."""

    summary: str
    start: datetime          # tz-aware UTC
    end: datetime | None     # tz-aware UTC (None for all-day)
    all_day: bool
    location: str | None
    link: str | None         # Meet link (hangoutLink) or the event's htmlLink

# Uzbek-friendly note shown when Google is not configured.
_NOT_CONFIGURED_UZ = (
    "Google Calendar ulanmagan. .env faylda GOOGLE_CLIENT_ID, "
    "GOOGLE_CLIENT_SECRET va GOOGLE_OAUTH_REFRESH_TOKEN ni to'ldiring."
)


def _parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 timestamp (``...Z`` or with offset) to tz-aware UTC."""
    # Python 3.11+ ``fromisoformat`` accepts the trailing 'Z'.
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class GoogleCalendarService:
    """Wraps the Google Calendar v3 API using OAuth credentials."""

    def __init__(
        self,
        creds: object | None,
        *,
        timezone: str = "Asia/Tashkent",
        work_start_hour: int = 9,
        work_end_hour: int = 18,
    ) -> None:
        self._creds = creds
        self._service: Any | None = None
        self._tz = ZoneInfo(timezone)
        self._work_start_hour = work_start_hour
        self._work_end_hour = work_end_hour

    def available(self) -> bool:
        """True when OAuth credentials are present (calls can be attempted)."""
        return self._creds is not None

    def _client(self) -> Any:
        """Lazily build (and cache) the discovery client."""
        if self._service is None:
            if self._creds is None:
                raise RuntimeError(_NOT_CONFIGURED_UZ)
            from googleapiclient.discovery import build

            self._service = build(
                "calendar", "v3", credentials=self._creds, cache_discovery=False
            )
        return self._service

    # ── listing events ──────────────────────────────────────────────────────
    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalEvent]:
        """Return calendar events in ``[start, end)`` (timed + all-day), time-ordered."""

        def _call() -> list[CalEvent]:
            service = self._client()
            resp = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start.astimezone(UTC).isoformat(),
                    timeMax=end.astimezone(UTC).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=max_results,
                )
                .execute()
            )
            out: list[CalEvent] = []
            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                s, en = e.get("start", {}), e.get("end", {})
                if s.get("dateTime"):
                    start_dt = _parse_rfc3339(s["dateTime"])
                    end_dt = _parse_rfc3339(en["dateTime"]) if en.get("dateTime") else None
                    all_day = False
                elif s.get("date"):
                    # All-day: a bare date -> local midnight -> UTC.
                    local = datetime.fromisoformat(s["date"]).replace(tzinfo=self._tz)
                    start_dt = local.astimezone(UTC)
                    end_dt = None
                    all_day = True
                else:
                    continue
                out.append(
                    CalEvent(
                        summary=e.get("summary") or "(nomsiz)",
                        start=start_dt,
                        end=end_dt,
                        all_day=all_day,
                        location=e.get("location"),
                        link=e.get("hangoutLink") or e.get("htmlLink"),
                    )
                )
            return out

        return await asyncio.to_thread(_call)

    # ── free / busy ───────────────────────────────────────────────────────
    async def get_busy(
        self, *, start: datetime, end: datetime, calendar_id: str = "primary"
    ) -> list[tuple[datetime, datetime]]:
        """Return busy intervals (UTC) in ``[start, end)`` from a free/busy query."""

        def _call() -> list[tuple[datetime, datetime]]:
            service = self._client()
            body = {
                "timeMin": start.astimezone(UTC).isoformat(),
                "timeMax": end.astimezone(UTC).isoformat(),
                "items": [{"id": calendar_id}],
            }
            resp = service.freebusy().query(body=body).execute()
            cal = resp.get("calendars", {}).get(calendar_id, {})
            return [
                (_parse_rfc3339(b["start"]), _parse_rfc3339(b["end"]))
                for b in cal.get("busy", [])
            ]

        return await asyncio.to_thread(_call)

    async def find_free_slots(
        self,
        *,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[tuple[datetime, datetime]]:
        """Return free intervals (UTC) of at least ``duration_minutes`` within the
        owner's working hours across ``[start, end)``.
        """
        busy = await self.get_busy(start=start, end=end, calendar_id=calendar_id)
        windows = self._working_windows(start, end)
        free = self._subtract_busy(windows, busy)
        need = timedelta(minutes=duration_minutes)
        return [(s, e) for (s, e) in free if e - s >= need]

    def _working_windows(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Working-hour windows (UTC) per local day intersected with [start,end)."""
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        windows: list[tuple[datetime, datetime]] = []
        day = start.astimezone(self._tz).date()
        last = end.astimezone(self._tz).date()
        while day <= last:
            w_start = datetime.combine(
                day, time(self._work_start_hour, 0), tzinfo=self._tz
            ).astimezone(UTC)
            w_end = datetime.combine(
                day, time(self._work_end_hour, 0), tzinfo=self._tz
            ).astimezone(UTC)
            lo = max(w_start, start)
            hi = min(w_end, end)
            if lo < hi:
                windows.append((lo, hi))
            day = day + timedelta(days=1)
        return windows

    @staticmethod
    def _subtract_busy(
        windows: list[tuple[datetime, datetime]],
        busy: list[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        """Subtract busy intervals from each window, returning free sub-intervals."""
        busy_sorted = sorted(busy)
        free: list[tuple[datetime, datetime]] = []
        for w_start, w_end in windows:
            cursor = w_start
            for b_start, b_end in busy_sorted:
                if b_end <= cursor or b_start >= w_end:
                    continue
                if b_start > cursor:
                    free.append((cursor, min(b_start, w_end)))
                cursor = max(cursor, b_end)
                if cursor >= w_end:
                    break
            if cursor < w_end:
                free.append((cursor, w_end))
        return free

    # ── event creation ────────────────────────────────────────────────────
    async def create_event_with_meet(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime,
        attendees: list[str] | None = None,
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Create a calendar event with an attached Meet link; return the event."""

        def _call() -> dict[str, Any]:
            service = self._client()
            event: dict[str, Any] = {
                "summary": title,
                "start": {"dateTime": start.astimezone(UTC).isoformat()},
                "end": {"dateTime": end.astimezone(UTC).isoformat()},
                "conferenceData": {
                    "createRequest": {
                        "requestId": uuid.uuid4().hex,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            }
            if attendees:
                event["attendees"] = [{"email": e} for e in attendees]
            return (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body=event,
                    conferenceDataVersion=1,
                    sendUpdates="all" if attendees else "none",
                )
                .execute()
            )

        return await asyncio.to_thread(_call)
