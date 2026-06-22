"""Gemini router tests — provider selection, schema build, parsing (no network)."""

from __future__ import annotations

import pytest

from app.brain.gemini_router import GeminiIntentRouter, _strip_unsupported
from app.brain.intent_router import IntentRouter
from app.brain.router_factory import build_router


# ── fakes: a Gemini client whose generate_content returns canned function calls ─
class _FakeCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _FakeResponse:
    def __init__(self, calls: list) -> None:
        self.function_calls = calls


class _FakeModels:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def generate_content(self, *, model, contents, config):
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.aio = type("Aio", (), {"models": _FakeModels(response)})()


_WHEN = {"raw": "10 minutda", "kind": "relative", "rel_minutes": 10, "clock_hint": None}


def test_build_router_selects_provider(settings):
    gemini = build_router(settings.model_copy(update={"llm_provider": "gemini"}))
    assert isinstance(gemini, GeminiIntentRouter)
    anthropic = build_router(settings.model_copy(update={"llm_provider": "anthropic"}))
    assert isinstance(anthropic, IntentRouter)


def test_strip_unsupported_removes_additional_properties():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "string", "additionalProperties": False}},
    }
    out = _strip_unsupported(schema)
    assert "additionalProperties" not in out
    assert "additionalProperties" not in out["properties"]["x"]


def test_tools_build_as_function_declarations():
    # No client needed to build the tool declarations from the shared schemas.
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse([])))
    tool = router._tools()
    assert len(tool.function_declarations) == 22


async def test_route_maps_function_call_to_intent():
    call = _FakeCall(
        "create_reminder",
        {"text": "suv ich", "when": _WHEN, "pre_alerts_minutes": []},
    )
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse([call])))
    routed = await router.route("suv ichishni esla", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "create_reminder"
    assert routed.params.text == "suv ich"


async def test_route_unknown_when_no_function_call():
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse([])))
    routed = await router.route("salom", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "unknown"
    assert routed.params is None


async def test_route_invalid_args_become_unknown():
    # Missing required 'text' -> pydantic validation fails -> unknown, no crash.
    call = _FakeCall("create_reminder", {"when": _WHEN})
    router = GeminiIntentRouter(client=_FakeClient(_FakeResponse([call])))
    routed = await router.route("...", now_iso="2026-06-18T13:00:00+05:00")
    assert routed.name == "unknown"


async def test_route_requires_client():
    # No client and no Gemini key configured in tests -> clear RuntimeError.
    router = GeminiIntentRouter(model="gemini-2.0-flash")
    assert router.client is None
    with pytest.raises(RuntimeError):
        await router.route("salom", now_iso="2026-06-18T13:00:00+05:00")


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
    good = _FakeResponse(
        [_FakeCall("create_reminder", {"text": "x", "when": _WHEN, "pre_alerts_minutes": []})]
    )
    router = GeminiIntentRouter(client=_flaky_client(2, good, counter))
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
    router = GeminiIntentRouter(client=_flaky_client(99, _FakeResponse([]), counter))
    with pytest.raises(genai_errors.ServerError):
        await router.route("...", now_iso="2026-06-18T13:00:00+05:00")
    assert counter["n"] == 3  # exactly _RETRY_ATTEMPTS tries
