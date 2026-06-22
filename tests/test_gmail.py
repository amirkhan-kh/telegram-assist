"""Gmail tests — header parsing + the list_emails dispatch path (no network)."""

from __future__ import annotations

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ListEmails
from app.db.base import utcnow
from app.integrations.google.gmail import EmailSummary, _clean_sender, _clean_snippet
from app.services.dispatcher import dispatch


def test_clean_sender_variants():
    assert _clean_sender('"Ali Valiyev" <ali@mail.com>') == "Ali Valiyev"
    assert _clean_sender("Bob <bob@x.com>") == "Bob"
    assert _clean_sender("solo@mail.com") == "solo"
    assert _clean_sender("") == "Noma'lum"


def test_clean_snippet_truncates():
    long = "x" * 200
    out = _clean_snippet(long, limit=20)
    assert len(out) == 20 and out.endswith("…")
    assert _clean_snippet("  a   b  ") == "a b"


class _FakeGmail:
    def __init__(self, emails: list[EmailSummary]) -> None:
        self._emails = emails

    def available(self) -> bool:
        return True

    async def list_unread(self, *, max_results: int = 5, query: str = "") -> list:
        return self._emails[:max_results]


async def test_list_emails_dispatch_renders(registry):
    registry.gmail_service = _FakeGmail(
        [
            EmailSummary("Investor", "Shartnoma", "Salom, shartnoma tayyor", True),
            EmailSummary("Bank", "Hisob", "Oylik hisobotingiz", False),
        ]
    )
    result = await dispatch(
        registry, RoutedIntent("list_emails", ListEmails(limit=5), {}), now=utcnow()
    )
    assert "O'qilmagan xatlar" in result.text
    assert "Investor" in result.text
    assert "Shartnoma" in result.text
    assert "⭐" in result.text  # the important one is flagged


async def test_list_emails_not_configured(registry):
    registry.gmail_service = None
    result = await dispatch(
        registry, RoutedIntent("list_emails", ListEmails(), {}), now=utcnow()
    )
    assert "Gmail ulanmagan" in result.text


async def test_list_emails_empty(registry):
    registry.gmail_service = _FakeGmail([])
    result = await dispatch(
        registry, RoutedIntent("list_emails", ListEmails(), {}), now=utcnow()
    )
    assert "O'qilmagan muhim xat yo'q" in result.text
