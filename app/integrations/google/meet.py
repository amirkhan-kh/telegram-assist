"""Google Meet link helpers (Milestone 3).

A Meet link is produced as a side effect of creating a Calendar event with a
``conferenceData`` create-request, so this module is a thin helper over
:class:`app.integrations.google.calendar.GoogleCalendarService`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.integrations.google.calendar import GoogleCalendarService

logger = get_logger(__name__)


def extract_meet_link(event: dict[str, Any]) -> str | None:
    """Pull the Meet ``hangoutLink`` (or conference entry point) from an event."""
    if not event:
        return None
    link = event.get("hangoutLink")
    if link:
        return str(link)
    conf = event.get("conferenceData") or {}
    for entry in conf.get("entryPoints", []) or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return str(entry["uri"])
    return None


async def create_meet_link(
    calendar_service: GoogleCalendarService,
    *,
    title: str,
    start: datetime,
    end: datetime,
    attendees: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Create an event with a Meet link.

    Returns ``(meet_link, gcal_event_id)``; the link may be ``None`` if Google
    did not attach one.
    """
    event = await calendar_service.create_event_with_meet(
        title=title, start=start, end=end, attendees=attendees
    )
    return extract_meet_link(event), event.get("id")
