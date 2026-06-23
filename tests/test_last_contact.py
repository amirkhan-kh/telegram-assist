"""Tests for the short-term contact memory (conversational coreference).

After the owner acts on a named contact (send/schedule/meeting/assign), a vague
follow-up — a bare pronoun ("unga"), a "<dem> <person>" phrase ("o'sha odamga"),
or no name at all — should resolve to that same contact. The memory is in-memory
and stays until a NEW contact name is used (which overwrites it). All offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ScheduleMeeting, SendMessage, TimeSpec
from app.repositories import meeting_repo, person_repo
from app.services.dispatcher import (
    _is_contact_reference,
    _refers_to_last_contact,
    clear_last_contact,
    clear_pending_outbound,
    dispatch,
)


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_memory(settings):
    """Module-global memory leaks across tests in one process — clear it."""
    owner = settings.owner_chat_id
    clear_last_contact(owner)
    clear_pending_outbound(owner)
    yield
    clear_last_contact(owner)
    clear_pending_outbound(owner)


# ── pure reference detection ──────────────────────────────────────────────────
def test_refers_to_last_contact_matches_pronouns():
    for phrase in ("u", "uni", "unga", "o'sha", "o'shanga", "shunga", "o'ziga"):
        assert _refers_to_last_contact(phrase), phrase


def test_refers_to_last_contact_matches_dem_plus_noun():
    for phrase in (
        "o'sha odamga", "shu kishiga", "o'sha bolaga", "shu qizga",
        "o'sha opaga", "u kontaktga", "ushbu insonga",
    ):
        assert _refers_to_last_contact(phrase), phrase


def test_refers_to_last_contact_rejects_real_names():
    # A generic word with NO demonstrative is treated as a real name, not a ref.
    for phrase in ("Dilshod", "Akmal aka", "Odamga", "Qizga", "Bola Karimov"):
        assert not _refers_to_last_contact(phrase), phrase


def test_is_contact_reference_treats_empty_as_reference():
    assert _is_contact_reference("") is True
    assert _is_contact_reference("   ") is True
    assert _is_contact_reference("unga") is True
    assert _is_contact_reference("Dilshod") is False


# ── end-to-end coreference through dispatch ───────────────────────────────────
async def _send_voice(registry, recipient_name: str, content: str):
    routed = RoutedIntent(
        "send_message",
        SendMessage(recipient_name=recipient_name, content=content, delivery="voice"),
        {},
    )
    return await dispatch(registry, routed, now=_now())


async def test_pronoun_followup_resolves_to_last_contact(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=777, display_name="Dilshod"
        )
    # Name the contact once (explicit voice => sends immediately, remembers it).
    first = await _send_voice(registry, "Dilshod", "salom")
    assert "Dilshod" in first.text and "yuborildi" in first.text

    # The follow-up names nobody but "unga" -> resolves back to Dilshod.
    second = await _send_voice(registry, "unga", "qandaysiz")
    assert "Dilshod" in second.text
    assert "yuborildi" in second.text
    assert "aniqlay olmadim" not in second.text


async def test_meeting_followup_targets_last_contact(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        dilshod = await person_repo.upsert_telegram_contact(
            session, telegram_user_id=778, display_name="Dilshod"
        )
        owner_id, dilshod_id = owner.id, dilshod.id

    await _send_voice(registry, "Dilshod", "salom")

    # "o'sha odamga 2 soatdan keyin uchrashuv" -> meeting whose target is Dilshod.
    routed = RoutedIntent(
        "schedule_meeting",
        ScheduleMeeting(
            title="Uchrashuv",
            when=TimeSpec(raw="2 soatdan keyin"),
            notify_target_name="o'sha odamga",
            create_meet_link=False,
        ),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "rejalashtirildi" in result.text
    assert "aniqlay olmadim" not in result.text

    async with registry.session() as session:
        meetings = await meeting_repo.list_upcoming(session, owner_id)
    assert len(meetings) == 1
    assert meetings[0].notify_target_ref == str(dilshod_id)


async def test_new_name_overrides_remembered_contact(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=779, display_name="Dilshod"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=780, display_name="Akmal"
        )
    await _send_voice(registry, "Dilshod", "salom")
    # A new explicit name replaces the memory...
    await _send_voice(registry, "Akmal", "salom")
    # ...so the next pronoun points at Akmal, not Dilshod.
    result = await _send_voice(registry, "unga", "qandaysiz")
    assert "Akmal" in result.text
    assert "Dilshod" not in result.text


async def test_pronoun_without_memory_asks_for_name(registry):
    # No contact named yet this session -> a pronoun cannot be resolved.
    result = await _send_voice(registry, "unga", "salom")
    assert "aniqlay olmadim" in result.text
