"""Multi-action routing: one utterance -> several ordered intents (3–5+)."""

from __future__ import annotations

from app.bot.handlers import _dispatch_routed_many, _force_chain_delivery
from app.brain.intent_router import RoutedIntent
from app.brain.intents import CreateReminder, DeliveryMode, SendMessage, TimeSpec
from app.repositories import person_repo
from app.services.dispatcher import clear_last_contact, clear_pending_outbound
from app.services.nlu_service import NluService, _looks_multi_action

_NOW = "2026-06-25T12:00:00+00:00"


def test_looks_multi_action():
    assert _looks_multi_action("Doniyorga xabar yubor va Dilnozaga qo'ng'iroq qil")
    assert _looks_multi_action("Asadbekka ayt, hozir ogohlantir va keyin eslat")
    # A genuine single command — even with two recipients — is NOT multi-action.
    assert not _looks_multi_action("Asadbekka salom yubor")
    assert not _looks_multi_action("Asadbek va Dilnozaga xabar yubor")


class _FakeRouter:
    """Stands in for the provider router; returns canned intents."""

    def __init__(self, many: list[RoutedIntent]) -> None:
        self._many = many
        self.client = object()

    async def route(self, utterance, *, now_iso):
        return self._many[0]

    async def route_many(self, utterance, *, now_iso):
        return self._many


def _send(name: str) -> RoutedIntent:
    return RoutedIntent(
        "send_message", SendMessage(recipient_name=name, content="x"), {}
    )


async def test_route_many_returns_all_ordered_actions(registry):
    nlu = NluService(registry)
    intents = [
        _send("Doniyor"),
        _send("Dilnoza"),
        RoutedIntent(
            "create_reminder",
            CreateReminder(
                text="suv ich",
                when=TimeSpec(raw="10 minutda", kind="relative", rel_minutes=10),
                pre_alerts_minutes=[],
            ),
            {},
        ),
    ]
    nlu._router = _FakeRouter(intents)
    got = await nlu.route_many(
        "Doniyorga xabar yubor, Dilnozaga ayt va keyin suv ichishni eslat",
        now_iso=_NOW,
    )
    assert [r.name for r in got] == ["send_message", "send_message", "create_reminder"]
    assert [got[0].params.recipient_name, got[1].params.recipient_name] == [
        "Doniyor",
        "Dilnoza",
    ]


async def test_route_many_drops_unknown_actions(registry):
    nlu = NluService(registry)
    nlu._router = _FakeRouter(
        [_send("Doniyor"), RoutedIntent("unknown", None, {}), _send("Dilnoza")]
    )
    got = await nlu.route_many(
        "Doniyorga ayt va Dilnozaga xabar yubor", now_iso=_NOW
    )
    assert [r.name for r in got] == ["send_message", "send_message"]


async def test_route_many_single_action_not_split(registry):
    nlu = NluService(registry)
    nlu._router = _FakeRouter([_send("Ignored")])
    got = await nlu.route_many("Asadbekka salom yubor", now_iso=_NOW)
    assert len(got) == 1


def test_force_chain_delivery_defaults_ask_to_text():
    intent = _send("Amirxon")
    assert intent.params.delivery == DeliveryMode.ask  # SendMessage default
    _force_chain_delivery(intent)
    assert intent.params.delivery == DeliveryMode.text


def test_force_chain_delivery_keeps_explicit_channel():
    intent = RoutedIntent(
        "send_message",
        SendMessage(recipient_name="Amirxon", content="x", delivery="voice"),
        {},
    )
    _force_chain_delivery(intent)
    assert intent.params.delivery == DeliveryMode.voice


async def test_chain_continues_past_send_prompt(registry):
    """A send's «Ovozli|Matn» prompt must NOT strand the rest of the chain."""
    owner = registry.settings.owner_chat_id
    clear_last_contact(owner)
    clear_pending_outbound(owner)
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=991, display_name="Amirxon"
        )
    items = [
        RoutedIntent(  # delivery defaults to ask -> would prompt if left alone
            "send_message", SendMessage(recipient_name="Amirxon", content="salom"), {}
        ),
        RoutedIntent(
            "send_message", SendMessage(recipient_name="Amirxon", content="yana"), {}
        ),
    ]
    result = await _dispatch_routed_many(registry, items)
    # Both ran -> combined summary, not a single delivery prompt.
    assert "Ketma-ket bajarilgan amallar" in result.text
    clear_pending_outbound(owner)
    clear_last_contact(owner)
