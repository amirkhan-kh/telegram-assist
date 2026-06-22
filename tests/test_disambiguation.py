"""Tests for same-name (Latin/Cyrillic) contact disambiguation + numbered pick."""

from __future__ import annotations

from datetime import UTC, datetime

from app.bot.handlers import _parse_selection
from app.brain.intent_router import RoutedIntent
from app.brain.intents import ScheduleMeeting, SendMessage, TimeSpec
from app.repositories import person_repo
from app.services import dispatcher
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


async def _two_akmals(registry) -> int:
    """Create a Latin and a Cyrillic 'Akmal'; return the owner key."""
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=501, display_name="Akmal"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=502, display_name="Акмал"
        )
    return registry.settings.owner_chat_id


# ── _parse_selection ──────────────────────────────────────────────────────────
def test_parse_selection_accepts_bare_numbers_and_ordinals():
    assert _parse_selection("1") == 1
    assert _parse_selection(" 2 ") == 2
    assert _parse_selection("2-chi") == 2
    assert _parse_selection("3.") == 3
    assert _parse_selection("birinchi") == 1
    assert _parse_selection("ikkinchi") == 2


def test_parse_selection_ignores_real_commands():
    assert _parse_selection("1 soatdan keyin esla") is None
    assert _parse_selection("salom") is None
    assert _parse_selection("Akmalga yoz") is None


# ── send_message disambiguation -> pick ───────────────────────────────────────
async def test_send_message_disambiguation_then_select(registry):
    owner_key = await _two_akmals(registry)
    routed = RoutedIntent(
        "send_message", SendMessage(recipient_name="Akmal", content="salom"), {}
    )
    res = await dispatch(registry, routed, now=_now())
    # Numbered prompt naming both alphabets.
    assert "1." in res.text and "2." in res.text
    assert "lotin" in res.text and "kiril" in res.text
    assert dispatcher.has_pending(owner_key)

    # Pick #1 -> the Latin Akmal -> now asks the channel, disambiguation cleared.
    res2 = await dispatcher.resume_choice(registry, 1, now=_now())
    assert res2 is not None
    assert "Qanday yuboray" in res2.text
    assert not dispatcher.has_pending(owner_key)

    # Choosing text then actually delivers the message.
    from app.db.models.enums import SendMode

    res3 = await dispatcher.complete_outbound(registry, owner_key, SendMode.text)
    assert "xabar yuborildi" in res3.text


async def test_resume_choice_out_of_range_keeps_pending(registry):
    owner_key = await _two_akmals(registry)
    await dispatch(
        registry,
        RoutedIntent("send_message", SendMessage(recipient_name="Akmal", content="x"), {}),
        now=_now(),
    )
    res = await dispatcher.resume_choice(registry, 9, now=_now())
    assert res is not None and "raqam" in res.text
    assert dispatcher.has_pending(owner_key)  # still awaiting a valid pick


async def test_resume_choice_without_pending_returns_none(registry):
    dispatcher.clear_pending(registry.settings.owner_chat_id)
    assert await dispatcher.resume_choice(registry, 1, now=_now()) is None


# ── schedule_meeting disambiguation -> pick -> full meeting ───────────────────
async def test_meeting_disambiguation_then_select_creates_meeting(registry):
    owner_key = await _two_akmals(registry)
    routed = RoutedIntent(
        "schedule_meeting",
        ScheduleMeeting(
            title="Suhbat",
            when=TimeSpec(raw="ertaga soat 10"),
            notify_target_name="Akmal",
        ),
        {},
    )
    res = await dispatch(registry, routed, now=_now())
    assert "Qaysi biri" in res.text
    assert dispatcher.has_pending(owner_key)

    res2 = await dispatcher.resume_choice(registry, 2, now=_now())
    assert res2 is not None
    assert "Uchrashuv rejalashtirildi" in res2.text
    assert "Suhbat" in res2.text
    assert not dispatcher.has_pending(owner_key)
