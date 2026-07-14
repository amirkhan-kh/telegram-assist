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
        self,
        *,
        max_results: int = 5,
        query: str = "is:unread in:inbox -category:promotions -category:social",
        filter_bulk: bool = True,
    ) -> list[EmailSummary]:
        """Return the newest *genuinely personal* unread inbox messages.

        "Muhim xatlar" should be real mail, not marketing. Two filters keep it
        clean: Gmail's Promotions/Social tabs are excluded server-side via the
        query, and bulk newsletters — those carrying a ``List-Unsubscribe`` /
        ``List-Id`` header or a bulk ``Precedence`` (product blasts from the
        likes of Claude, Notion or Artlist) — are dropped unless Gmail itself
        flagged the message IMPORTANT. We over-fetch a candidate pool so the
        post-filter still yields up to ``max_results`` survivors.
        """

        def _call() -> list[EmailSummary]:
            service = self._client()
            # Over-fetch: some candidates get filtered out as bulk/marketing.
            fetch_n = max(max_results * 4, max_results + 5) if filter_bulk else max_results
            listing = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=fetch_n)
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
                        metadataHeaders=[
                            "From",
                            "Subject",
                            "List-Unsubscribe",
                            "List-Id",
                            "Precedence",
                        ],
                    )
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                labels = msg.get("labelIds") or []
                important = "IMPORTANT" in labels
                if filter_bulk and not important and _is_bulk(headers, labels):
                    continue
                out.append(
                    EmailSummary(
                        sender=_clean_sender(headers.get("from", "")),
                        subject=(headers.get("subject") or "(mavzusiz)").strip(),
                        snippet=_clean_snippet(msg.get("snippet", "")),
                        important=important,
                    )
                )
                if len(out) >= max_results:
                    break
            return out

        return await asyncio.to_thread(_call)


def _is_bulk(headers: dict[str, str], labels: list[str]) -> bool:
    """True for mass/marketing mail (newsletters, product blasts, promotions).

    Signals, strongest first: a ``List-Unsubscribe`` / ``List-Id`` header (present
    on virtually every marketing/newsletter blast, absent on personal mail), a
    bulk ``Precedence``, or Gmail's Promotions/Social category labels.
    """
    if "list-unsubscribe" in headers or "list-id" in headers:
        return True
    precedence = (headers.get("precedence") or "").strip().lower()
    if precedence in {"bulk", "list", "junk"}:
        return True
    return any(
        label in {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"} for label in labels
    )


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
