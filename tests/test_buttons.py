"""Tests for quick-menu buttons and the loading→result reply flow."""

from __future__ import annotations

import asyncio

from app.bot.handlers import _dispatch_routed_many, _menu_intent, _respond
from app.repositories import person_repo
from app.services.dispatcher import DispatchResult
from app.services.nlu_service import _direct_meeting_sequence


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
    assert message.sent.edits[-1] == (
        "✅ Amal bajarildi.\n\nNatija",
        "HTML",
    )                                                       # then edited to result


async def test_respond_does_not_mark_prompt_as_done():
    message = _FakeMessage()

    async def run() -> DispatchResult:
        return DispatchResult("Qanday yuboray?")

    await _respond(message, run)
    assert message.sent.edits[-1] == ("Qanday yuboray?", None)


async def test_respond_falls_back_to_plain_on_bad_html():
    message = _FakeMessage(fail_html=True)

    async def run() -> DispatchResult:
        return DispatchResult("<b>Salom</b>", parse_mode="HTML")

    await _respond(message, run)
    # HTML edit failed -> retried with tags stripped, no parse_mode.
    assert message.sent.edits[-1] == ("✅ Amal bajarildi.\n\nSalom", None)


async def test_respond_handles_error_gracefully():
    message = _FakeMessage()

    async def run() -> DispatchResult:
        raise RuntimeError("boom")

    await _respond(message, run)
    # The loading placeholder is edited with a user-facing error (not crashed).
    assert message.sent.edits  # something was written
    assert "Xatolik" in message.sent.edits[-1][0]


async def test_respond_auto_retries_after_rate_limit(monkeypatch):
    from app.bot import handlers

    message = _FakeMessage()
    calls = 0
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(handlers, "_retry_after_seconds", lambda _exc: 1)
    monkeypatch.setattr(handlers.asyncio, "sleep", fast_sleep)

    async def run() -> DispatchResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED retryDelay: 1s")
        return DispatchResult("Qayta urinish natijasi")

    await _respond(message, run)
    await real_sleep(0)

    assert calls == 2
    assert message.sent.edits[-1] == (
        "✅ Amal bajarildi.\n\nQayta urinish natijasi",
        None,
    )


async def test_dispatch_routed_many_runs_meeting_and_notifications(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=9901, display_name="Asadbek"
        )

    routed = _direct_meeting_sequence(
        "Asadbek bilan ertaga soat 10:00 da miting belgila va hozir ogohlantir "
        "va 12:00 da bir marta, 15:00 da bir marta xabardor qil"
    )
    assert routed is not None

    before = len(registry.scheduler.get_jobs())
    result = await _dispatch_routed_many(registry, routed)
    after = len(registry.scheduler.get_jobs())

    assert isinstance(result, DispatchResult)
    assert "Ketma-ket bajarilgan amallar" in result.text
    assert "Uchrashuv rejalashtirildi" in result.text
    assert "Xabar yuborildi" in result.text
    assert result.text.count("Xabar rejalashtirildi") == 2
    assert after >= before + 2
