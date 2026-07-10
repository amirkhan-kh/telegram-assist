"""Gemini router tests — provider selection + structured-output parsing (no network)."""

from __future__ import annotations

import pytest

from app.brain.gemini_router import GeminiIntentRouter
from app.brain.intent_router import IntentRouter
from app.brain.intents import CreateReminder, SendMessage, TimeSpec
from app.brain.nlu_schema import NLUMultiResult, NLUResult
from app.brain.router_factory import build_router


# ── fakes: a Gemini client whose generate_content returns a canned response ────
class _FakeResponse:
    """Mimics a google-genai response: ``.parsed`` (SDK-validated) + ``.text``."""

    def __init__(self, parsed: NLUResult | None = None, text: str | None = None) -> None:
        self.parsed = parsed
        self.text = text


class _FakeModels:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def generate_content(self, *, model, contents, config):
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.aio = type("Aio", (), {"models": _FakeModels(response)})()


_WHEN = TimeSpec(raw="10 minutda", kind="relative", rel_minutes=10)


def _reminder_result() -> NLUResult:
    return NLUResult(
        reasoning="owner reminder",
        intent="create_reminder",
        create_reminder=CreateReminder(text="suv ich", when=_WHEN, pre_alerts_minutes=[]),
    )


def test_build_router_selects_provider(settings):
    gemini = build_router(settings.model_copy(update={"llm_provider": "gemini"}))
    assert isinstance(gemini, GeminiIntentRouter)
    anthropic = build_router(settings.model_copy(update={"llm_provider": "anthropic"}))
    assert isinstance(anthropic, IntentRouter)


async def test_route_maps_structured_result_to_intent():
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=_reminder_result())))
    routed = await router.route("suv ichishni esla", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "create_reminder"
    assert routed.params.text == "suv ich"


async def test_route_parses_from_json_text_when_no_parsed():
    # An SDK/fake that only fills ``.text`` (raw JSON) still routes correctly.
    response = _FakeResponse(parsed=None, text=_reminder_result().model_dump_json())
    router = GeminiIntentRouter(client=_FakeClient(response))
    routed = await router.route("suv ich", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "create_reminder"
    assert routed.params.text == "suv ich"


async def test_route_unknown_intent_returns_unknown():
    result = NLUResult(reasoning="greeting, no intent", intent="unknown")
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=result)))
    routed = await router.route("salom", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "unknown"
    assert routed.params is None


async def test_route_adopts_filled_subobject_on_intent_mismatch():
    # Model named create_reminder but actually filled send_message -> adopt it.
    result = NLUResult(
        reasoning="mismatch",
        intent="create_reminder",
        create_reminder=None,
        send_message=SendMessage(recipient_name="Akmal", content="salom"),
    )
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=result)))
    routed = await router.route("Akmalga salom yoz", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "send_message"
    assert routed.params.recipient_name == "Akmal"


async def test_route_invalid_json_becomes_unknown():
    # Sub-object missing a required field -> validation fails -> unknown, no crash.
    bad = '{"reasoning":"x","intent":"create_reminder","create_reminder":{"when":null}}'
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=None, text=bad)))
    routed = await router.route("...", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "unknown"


async def test_route_no_result_returns_unknown():
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=None, text=None)))
    routed = await router.route("...", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "unknown"
    assert routed.params is None


async def test_route_requires_client():
    # No client and no Gemini key configured in tests -> clear RuntimeError.
    router = GeminiIntentRouter(model="gemini-2.5-flash")
    assert router.client is None
    with pytest.raises(RuntimeError):
        await router.route("salom", now_iso="2026-06-18T13:00:00+05:00")


def _multi_result() -> NLUMultiResult:
    return NLUMultiResult(
        actions=[
            NLUResult(
                reasoning="notify now",
                intent="send_message",
                send_message=SendMessage(recipient_name="Doniyor", content="salom"),
            ),
            NLUResult(
                reasoning="second send",
                intent="send_message",
                send_message=SendMessage(recipient_name="Dilnoza", content="qo'ng'iroq"),
            ),
            NLUResult(
                reasoning="reminder",
                intent="create_reminder",
                create_reminder=CreateReminder(text="suv ich", when=_WHEN, pre_alerts_minutes=[]),
            ),
        ]
    )


async def test_route_many_splits_into_ordered_intents():
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse(parsed=_multi_result())))
    routed = await router.route_many("...", now_iso="2026-06-18T13:00:00+05:00")
    assert [r.name for r in routed] == ["send_message", "send_message", "create_reminder"]
    assert routed[0].params.recipient_name == "Doniyor"
    assert routed[1].params.recipient_name == "Dilnoza"


async def test_route_many_parses_from_json_text():
    response = _FakeResponse(parsed=None, text=_multi_result().model_dump_json())
    router = GeminiIntentRouter(client=_FakeClient(response))
    routed = await router.route_many("...", now_iso="2026-06-18T13:00:00+05:00")
    assert [r.name for r in routed] == ["send_message", "send_message", "create_reminder"]


async def test_route_many_empty_actions_falls_back_to_single():
    router = GeminiIntentRouter(
        client=_FakeClient(_FakeResponse(parsed=NLUMultiResult(actions=[])))
    )
    routed = await router.route_many("salom", now_iso="2026-06-18T13:00:00+05:00")
    assert len(routed) == 1


def _flaky_client(fail_times: int, response: _FakeResponse, counter: dict):
    """A client that raises a transient ServerError ``fail_times`` then succeeds."""
    from google.genai import errors as genai_errors

    class _Models:
        async def generate_content(self, *, model, contents, config):
            counter["n"] += 1
            if counter["n"] <= fail_times:
                raise genai_errors.ServerError(
                    503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}}
                )
            return response

    client = type("C", (), {})()
    client.aio = type("Aio", (), {"models": _Models()})()
    return client


async def test_route_retries_transient_server_error(monkeypatch):
    from app.brain import gemini_router as gr

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(gr.asyncio, "sleep", _no_sleep)  # instant backoff

    counter = {"n": 0}
    router = GeminiIntentRouter(
        client=_flaky_client(2, _FakeResponse(parsed=_reminder_result()), counter)
    )
    routed = await router.route("...", now_iso="2026-06-18T13:00:00+05:00")

    assert counter["n"] == 3  # two failures, then success
    assert routed.name == "create_reminder"


async def test_route_raises_after_retries_exhausted(monkeypatch):
    from google.genai import errors as genai_errors

    from app.brain import gemini_router as gr

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(gr.asyncio, "sleep", _no_sleep)

    counter = {"n": 0}
    router = GeminiIntentRouter(client=_flaky_client(99, _FakeResponse(), counter))
    with pytest.raises(genai_errors.ServerError):
        await router.route("...", now_iso="2026-06-18T13:00:00+05:00")
    assert counter["n"] == 3  # exactly _RETRY_ATTEMPTS tries
