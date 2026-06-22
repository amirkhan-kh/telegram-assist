"""Tests for the listing/viewing intents and Cyrillic⇄Latin name matching."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ListAgenda, ListContacts, ListFinance, ListReminders
from app.brain.translit import normalize_name
from app.db.models.enums import DebtDirection
from app.repositories import person_repo
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


# ── Cyrillic / Latin normalization ────────────────────────────────────────────
def test_normalize_cyrillic_equals_latin():
    assert normalize_name("Акмал") == normalize_name("Akmal")
    assert normalize_name("Шокир") == normalize_name("Shokir")
    assert normalize_name("Ўткир") == normalize_name("O'tkir")
    assert normalize_name("ВАЛИ") == normalize_name("vali")


async def test_resolve_contact_cyrillic_matches_latin(registry):
    from app.brain.contacts import ContactMatch, resolve_contact

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=909, display_name="Akmal Karimov"
        )
        resolved = await resolve_contact(session, "Акмал")
    assert isinstance(resolved, ContactMatch)
    assert resolved.chat_id == 909


async def test_past_reminder_drops_from_agenda(registry):
    """A reminder is transient: once its time passes it leaves the plan view."""
    now = datetime.now(UTC)
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="KELAJAK eslatma", when_dt=now + timedelta(hours=2)
    )
    await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="OTGAN eslatma", when_dt=now - timedelta(hours=2)
    )
    result = await dispatch(
        registry, RoutedIntent("list_agenda", ListAgenda(scope="all"), {}), now=now
    )
    assert "KELAJAK eslatma" in result.text          # upcoming -> shown
    assert "OTGAN eslatma" not in result.text        # past -> dropped
    assert "Muddati o'tgan" not in result.text       # reminders are not "overdue"


async def test_list_reminders_upcoming_and_recurring(registry):
    now = datetime.now(UTC)
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="KELAJAK ish", when_dt=now + timedelta(hours=3)
    )
    await registry.reminder_service.create_reminder(
        owner_id=owner.id, title="OTGAN ish", when_dt=now - timedelta(hours=3)
    )
    await registry.reminder_service.create_reminder(
        owner_id=owner.id,
        title="HAFTALIK ish",
        when_dt=now + timedelta(days=1),
        recurrence="Har dushanba 08:00",
        cron_fields={"day_of_week": "mon", "hour": 8, "minute": 0},
    )
    result = await dispatch(
        registry, RoutedIntent("list_reminders", ListReminders(), {}), now=now
    )
    assert "Eslatmalarim" in result.text
    assert "KELAJAK ish" in result.text                # upcoming one-shot
    assert "HAFTALIK ish" in result.text and "Har dushanba" in result.text
    assert "OTGAN ish" not in result.text              # past one-shot dropped


async def test_search_tolerates_typo(registry):
    # Missing/extra letters: "Azizilo" should still surface "Azizillo".
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=321, display_name="Azizillo"
        )
        found = await person_repo.search_by_name(session, "Azizilo")
    assert any(p.telegram_user_id == 321 for p in found)


async def test_search_no_false_positive_for_unrelated(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=654, display_name="Azizillo"
        )
        found = await person_repo.search_by_name(session, "Xolmurod")
    assert all(p.telegram_user_id != 654 for p in found)


# ── list_contacts ──────────────────────────────────────────────────────────────
async def test_list_contacts_lists_all(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=11, display_name="Akmal Karimov", username="akmalk"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=12, display_name="Bekzod Aliyev"
        )
    res = await dispatch(
        registry, RoutedIntent("list_contacts", ListContacts(), {}), now=_now()
    )
    assert "Akmal Karimov" in res.text
    assert "Bekzod Aliyev" in res.text
    assert "Owner" not in res.text  # the owner is excluded from the list


async def test_list_contacts_with_query_filters(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=21, display_name="Akmal Karimov"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=22, display_name="Bekzod Aliyev"
        )
    res = await dispatch(
        registry,
        RoutedIntent("list_contacts", ListContacts(query="bekzod"), {}),
        now=_now(),
    )
    assert "Bekzod" in res.text
    assert "Akmal" not in res.text


# ── list_finance ─────────────────────────────────────────────────────────────
async def test_list_finance_names_and_total(registry):
    async with registry.session() as session:
        vali = await person_repo.create(session, display_name="Vali")
        akmal = await person_repo.create(session, display_name="Akmal")
        vali_id, akmal_id = vali.id, akmal.id
    await registry.finance_service.add_entry(
        counterparty_id=vali_id, direction=DebtDirection.they_owe_me, amount=50000
    )
    await registry.finance_service.add_entry(
        counterparty_id=akmal_id, direction=DebtDirection.they_owe_me, amount=200000
    )
    res = await dispatch(
        registry,
        RoutedIntent("list_finance", ListFinance(direction="they_owe_me"), {}),
        now=_now(),
    )
    assert "Vali" in res.text
    assert "Akmal" in res.text
    assert "Jami" in res.text
    assert "250 000" in res.text  # computed total


async def test_list_finance_empty(registry):
    res = await dispatch(
        registry,
        RoutedIntent("list_finance", ListFinance(direction="i_owe_them"), {}),
        now=_now(),
    )
    assert "yo'q" in res.text


# ── list_agenda ──────────────────────────────────────────────────────────────
async def test_list_agenda_shows_reminder(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id
    await registry.reminder_service.create_reminder(
        owner_id=owner_id,
        title="suv ich",
        when_dt=datetime(2026, 6, 18, 10, 0, tzinfo=UTC),
    )
    res = await dispatch(
        registry, RoutedIntent("list_agenda", ListAgenda(), {}), now=_now()
    )
    assert "Eslatmalar" in res.text
    assert "suv ich" in res.text


async def test_list_agenda_empty(registry):
    res = await dispatch(
        registry, RoutedIntent("list_agenda", ListAgenda(), {}), now=_now()
    )
    assert "bo'sh" in res.text


# ── list_meetings ─────────────────────────────────────────────────────────────
async def test_list_meetings_shows_scheduled(registry):
    from app.brain.intents import ListMeetings

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id
    await registry.meeting_service.create_meeting(
        owner_id=owner_id,
        title="Bexruz bilan",
        start_at=datetime(2026, 6, 19, 19, 57, tzinfo=UTC),
        end_at=datetime(2026, 6, 19, 20, 27, tzinfo=UTC),
        meet_link="https://meet.google.com/abc-defg-hij",
    )
    res = await dispatch(
        registry, RoutedIntent("list_meetings", ListMeetings(), {}), now=_now()
    )
    assert "Bexruz bilan" in res.text
    assert "meet.google.com/abc-defg-hij" in res.text


async def test_list_meetings_empty(registry):
    from app.brain.intents import ListMeetings

    res = await dispatch(
        registry, RoutedIntent("list_meetings", ListMeetings(), {}), now=_now()
    )
    assert "uchrashuv yo'q" in res.text
