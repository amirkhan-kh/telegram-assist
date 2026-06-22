"""End-to-end dispatch tests: a validated intent -> service -> DB + confirmation.

These bypass the Anthropic call by constructing :class:`RoutedIntent` directly,
so they exercise the dispatcher, the domain services, the scheduler hand-off and
the database without any network access.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.brain.intent_router import RoutedIntent
from app.brain.intents import AddFinance, CreatePromise, CreateReminder, TimeSpec
from app.db.models.enums import DebtDirection
from app.repositories import reminder_repo
from app.services.dispatcher import dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


async def test_create_reminder_persists_and_confirms(registry):
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="suv ich", when=TimeSpec(raw="10 minutda"), pre_alerts_minutes=[]),
        {},
    )
    result = await dispatch(registry, routed, now=_now())

    assert "Eslatma qo'yildi" in result.text
    assert "suv ich" in result.text

    async with registry.session() as session:
        owner = await _owner_id(session)
        reminders = await reminder_repo.list_active(session, owner)
    assert any(r.title == "suv ich" for r in reminders)


async def test_add_finance_credit_confirms(registry):
    routed = RoutedIntent(
        "add_finance",
        AddFinance(direction="credit", counterparty_name="Vali", amount=50000, currency="UZS"),
        {},
    )
    result = await dispatch(registry, routed, now=_now())

    assert "Vali" in result.text
    assert "50000" in result.text

    open_credits = await registry.finance_service.list_open(DebtDirection.they_owe_me)
    assert len(open_credits) == 1
    assert open_credits[0].counterparty_id is not None


async def test_add_finance_empty_due_does_not_ask_for_time(registry):
    # "Saydxonga 120 000 qarz berdim" with no time: the NLU may emit an empty
    # `due` TimeSpec — that must NOT trigger a "specify the time" prompt.
    routed = RoutedIntent(
        "add_finance",
        AddFinance(
            direction="credit",
            counterparty_name="Saydxon",
            amount=120000,
            due=TimeSpec(raw=""),
        ),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Qarz yozib qo'yildi" in result.text
    assert "Saydxon" in result.text
    assert "aniqroq" not in result.text.lower()  # no time-clarification prompt


async def test_settle_debt_closes_record_and_refreshes_list(registry):
    """Tapping «✅ <name> to'ladi» settles the debt and re-renders the open list."""
    from app.services.dispatcher import settle_debt

    # Owe-me debt: Vali owes 50000.
    await dispatch(
        registry,
        RoutedIntent(
            "add_finance",
            AddFinance(direction="credit", counterparty_name="Vali", amount=50000, currency="UZS"),
            {},
        ),
        now=_now(),
    )
    open_before = await registry.finance_service.list_open(DebtDirection.they_owe_me)
    assert len(open_before) == 1
    record_id = open_before[0].id

    # Settle it (dir_code "t" = they_owe_me view).
    toast, relist = await settle_debt(registry, record_id, "t", now=_now())
    assert "yopildi" in toast
    assert "Vali" in toast

    # The record drops out of the open-debts list.
    open_after = await registry.finance_service.list_open(DebtDirection.they_owe_me)
    assert open_after == []
    assert "Vali" not in relist.text


async def test_settle_debt_unknown_id_is_graceful(registry):
    """Settling a missing/already-closed id reports it without crashing."""
    from app.services.dispatcher import settle_debt

    toast, relist = await settle_debt(registry, 999999, "a", now=_now())
    assert "topilmadi" in toast.lower() or "yopilgan" in toast.lower()
    assert relist.text  # still renders a (empty) list view


async def test_self_promise_confirms(registry):
    routed = RoutedIntent(
        "create_promise",
        CreatePromise(what="hisobotni yuborish", deadline=TimeSpec(raw="ertaga soat 9")),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Va'da" in result.text
    assert "hisobotni yuborish" in result.text


async def test_unknown_intent_is_graceful(registry):
    result = await dispatch(registry, RoutedIntent("unknown", None, {}), now=_now())
    assert "Tushunmadim" in result.text


async def test_ambiguous_time_asks_day_then_clock_then_creates(registry):
    """A vague time pins the day via buttons + the clock via text before creating.

    "soat 22:00" (clock known, day missing) -> day keyboard; pick a day -> still
    no need to ask clock (already known) -> the reminder is created. Nothing is
    created until the time is fully resolved.
    """
    from app.services import dispatcher

    owner_key = registry.settings.owner_chat_id
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="futbol", when=TimeSpec(raw="soat 22:00"), pre_alerts_minutes=[]),
        {},
    )
    res = await dispatch(registry, routed, now=_now())
    # Asks the DAY with a keyboard; no reminder yet.
    assert "kun" in res.text.lower()
    assert res.reply_markup is not None
    assert dispatcher.has_pending_time(owner_key) is False  # awaiting a DAY tap, not text

    # Pick "Ertaga" (tday:d1) -> clock already known (22:00) -> reminder created.
    done = await dispatcher.resume_time_day(registry, "d1", now=_now())
    assert done is not None
    assert "Eslatma qo'yildi" in done.text and "22:00" in done.text


async def test_ambiguous_time_day_known_asks_clock_via_text(registry):
    """"ertaga" (day known, clock missing) -> ask the clock as TYPED text."""
    from app.services import dispatcher

    owner_key = registry.settings.owner_chat_id
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="yig'ilish", when=TimeSpec(raw="ertaga"), pre_alerts_minutes=[]),
        {},
    )
    res = await dispatch(registry, routed, now=_now())
    assert "soat" in res.text.lower()
    assert res.reply_markup is None  # the clock is asked as text, not buttons
    assert dispatcher.has_pending_time(owner_key)  # awaiting a typed clock

    done = await dispatcher.resume_time_text(registry, "9:30", now=_now())
    assert done is not None and "Eslatma qo'yildi" in done.text and "09:30" in done.text
    assert not dispatcher.has_pending_time(owner_key)


# ── helper ────────────────────────────────────────────────────────────────────
async def _owner_id(session) -> int:
    from app.repositories import person_repo

    owner = await person_repo.get_owner(session)
    assert owner is not None
    return owner.id
