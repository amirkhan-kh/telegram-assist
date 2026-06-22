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


async def test_ambiguous_time_asks_for_clarification(registry):
    # A bare hour with no day/clock qualifier must produce a polite question,
    # not a crash or a generic error.
    routed = RoutedIntent(
        "create_reminder",
        CreateReminder(text="uchrashuv", when=TimeSpec(raw="9"), pre_alerts_minutes=[]),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "aniq emas" in result.text.lower() or "tushunolmadim" in result.text.lower()


# ── helper ────────────────────────────────────────────────────────────────────
async def _owner_id(session) -> int:
    from app.repositories import person_repo

    owner = await person_repo.get_owner(session)
    assert owner is not None
    return owner.id
