"""Gmail service (read-only) — surface the owner's important / unread mail.

A thin async wrapper over the Gmail v1 API, mirroring
:class:`app.integrations.google.calendar.GoogleCalendarService`: the discovery
client is built lazily, every blocking call runs through ``asyncio.to_thread``,
and when no OAuth credentials are present :meth:`available` is ``False`` so callers
degrade gracefully. Uses the same Google OAuth credentials as Calendar (the
``gmail.readonly`` scope must be granted — re-run ``scripts.google_auth`` once).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from app.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class EmailSummary:
    """A compact view of one inbox message for display."""

    sender: str
    subject: str
    snippet: str
    important: bool


class GmailService:
    """Read-only Gmail access: list important / unread inbox messages."""

    def __init__(self, creds: object | None) -> None:
        self._creds = creds
        self._service: Any | None = None

    def available(self) -> bool:
        """True when OAuth credentials are present (calls can be attempted)."""
        return self._creds is not None

    def _client(self) -> Any:
        """Lazily build (and cache) the Gmail discovery client."""
        if self._service is None:
            if self._creds is None:
                raise RuntimeError("Gmail not configured")
            from googleapiclient.discovery import build

            self._service = build(
                "gmail", "v1", credentials=self._creds, cache_discovery=False
            )
        return self._service

    async def list_unread(
        self, *, max_results: int = 5, query: str = "is:unread in:inbox"
    ) -> list[EmailSummary]:
        """Return the newest unread inbox messages (importance-flagged)."""

        def _call() -> list[EmailSummary]:
            service = self._client()
            listing = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            out: list[EmailSummary] = []
            for ref in listing.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject"],
                    )
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                out.append(
                    EmailSummary(
                        sender=_clean_sender(headers.get("from", "")),
                        subject=(headers.get("subject") or "(mavzusiz)").strip(),
                        snippet=_clean_snippet(msg.get("snippet", "")),
                        important="IMPORTANT" in (msg.get("labelIds") or []),
                    )
                )
            return out

        return await asyncio.to_thread(_call)


def _clean_sender(value: str) -> str:
    """Extract a human display name from a From header ('Name <e@mail>' -> 'Name')."""
    value = value.strip()
    match = re.match(r"^\s*\"?([^\"<]+?)\"?\s*<", value)
    if match:
        return match.group(1).strip()
    # Bare address: show the part before '@'.
    if "@" in value:
        return value.split("@", 1)[0].strip("<> ")
    return value or "Noma'lum"


def _clean_snippet(snippet: str, limit: int = 90) -> str:
    """Collapse whitespace and truncate a snippet for a tidy one-liner."""
    flat = " ".join((snippet or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
