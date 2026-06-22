"""Tests for quick-menu buttons and the loading→result reply flow."""

from __future__ import annotations

from app.bot.handlers import _menu_intent, _respond
from app.services.dispatcher import DispatchResult


# ── fake Telegram message that records replies/edits ──────────────────────────
class _FakeSent:
    def __init__(self, fail_html: bool = False) -> None:
        self.edits: list[tuple[str, str | None]] = []
        self._fail_html = fail_html

    async def edit_text(
        self,
        text: str,
        parse_mode: str | None = None,
        reply_markup: object | None = None,
    ) -> None:
        if self._fail_html and parse_mode is not None:
            raise RuntimeError("can't parse entities")
        self.edits.append((text, parse_mode))


class _FakeMessage:
    def __init__(self, fail_html: bool = False) -> None:
        self.replies: list[str] = []
        self.sent = _FakeSent(fail_html=fail_html)

    async def reply_text(self, text: str, **_kwargs: object) -> _FakeSent:
        self.replies.append(text)
        return self.sent


# ── _menu_intent ───────────────────────────────────────────────────────────────
def test_menu_intent_maps_each_button():
    assert _menu_intent("📋 Bugungi reja").name == "list_agenda"
    assert _menu_intent("📰 Yangiliklar").name == "get_digest"
    assert _menu_intent("📅 Kalendar").name == "show_calendar"
    assert _menu_intent("💰 Qarzlar").name == "list_finance"
    assert _menu_intent("📆 Muhim sanalar").name == "list_important_dates"
    assert _menu_intent("📓 Qarorlarim").name == "list_decisions"
    assert _menu_intent("oddiy xabar") is None


def test_menu_intent_agenda_is_today():
    routed = _menu_intent("📋 Bugungi reja")
    assert routed.params.scope == "today"


# ── _respond loading -> result ────────────────────────────────────────────────
async def test_respond_loading_then_result():
    message = _FakeMessage()

    async def run() -> DispatchResult:
        return DispatchResult("Natija", parse_mode="HTML")

    await _respond(message, run, loading="⏳ Yuklanmoqda…")
    assert message.replies[0] == "⏳ Yuklanmoqda…"          # loading shown first
    assert message.sent.edits[-1] == ("Natija", "HTML")     # then edited to result


async def test_respond_falls_back_to_plain_on_bad_html():
    message = _FakeMessage(fail_html=True)

    async def run() -> DispatchResult:
        return DispatchResult("<b>Salom</b>", parse_mode="HTML")

    await _respond(message, run)
    # HTML edit failed -> retried with tags stripped, no parse_mode.
    assert message.sent.edits[-1] == ("Salom", None)


async def test_respond_handles_error_gracefully():
    message = _FakeMessage()

    async def run() -> DispatchResult:
        raise RuntimeError("boom")

    await _respond(message, run)
    # The loading placeholder is edited with a user-facing error (not crashed).
    assert message.sent.edits  # something was written
    assert "Xatolik" in message.sent.edits[-1][0]
