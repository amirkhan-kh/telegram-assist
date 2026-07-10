"""Tests for the answer_question intent + AnswerService (no network).

Covers: the intent is registered on both the validation map and the structured
envelope; the dispatcher returns the model's reply; a missing Gemini client
degrades to a clear Uzbek message; and a grounding failure still yields an
answer via the ungrounded retry.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.brain.intent_router import INTENT_MODELS, RoutedIntent
from app.brain.intents import AnswerQuestion
from app.brain.nlu_schema import NLUResult
from app.services import answer_service
from app.services.dispatcher import dispatch


# ── fakes: a Gemini client whose generate_content returns canned text ──────────
class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    """Records the config of each call; replies with canned text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tool_calls: list[object] = []

    async def generate_content(self, *, model, contents, config):
        self.tool_calls.append(getattr(config, "tools", None))
        return _FakeResponse(self.text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)
        self.aio = type("Aio", (), {"models": self.models})()


class _GroundingFailsModels:
    """Raises while a search tool is attached; succeeds once it's dropped."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        if getattr(config, "tools", None):
            raise RuntimeError("grounding unavailable in this region")
        return _FakeResponse(self.text)


class _GroundingFailsClient:
    def __init__(self, text: str) -> None:
        self.models = _GroundingFailsModels(text)
        self.aio = type("Aio", (), {"models": self.models})()


class _RecordingModels:
    """Records the `contents` string of every call (for context assertions)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.contents: list[str] = []

    async def generate_content(self, *, model, contents, config):
        self.contents.append(contents)
        return _FakeResponse(self.text)


class _RecordingClient:
    def __init__(self, text: str) -> None:
        self.models = _RecordingModels(text)
        self.aio = type("Aio", (), {"models": self.models})()


def test_answer_question_is_registered():
    assert INTENT_MODELS["answer_question"] is AnswerQuestion
    result = NLUResult(
        reasoning="Owner asks a general question.",
        intent="answer_question",
        answer_question=AnswerQuestion(query="1 dollar necha so'm", needs_fresh_info=True),
    )
    assert result.answer_question is not None
    assert result.answer_question.needs_fresh_info is True


@pytest.mark.asyncio
async def test_answer_question_dispatch_returns_model_text(registry, monkeypatch):
    fake = _FakeClient("1 dollar hozir taxminan 12 600 so'm.")
    monkeypatch.setattr(answer_service, "get_gemini_client", lambda settings: fake)

    routed = RoutedIntent(
        "answer_question",
        AnswerQuestion(query="1 dollar necha so'm", needs_fresh_info=True),
        {},
    )
    result = await dispatch(registry, routed, now=datetime.now(UTC))
    assert "so'm" in result.text
    # Conversational answers are flagged to be spoken back on voice turns.
    assert result.speak is True


def test_speech_text_strips_markup_and_bullets():
    from app.bot.handlers import _speech_text

    raw = "**Javob:**\n- birinchi\n- ikkinchi\n\n#izoh"
    cleaned = _speech_text(raw)
    assert "*" not in cleaned and "#" not in cleaned
    assert cleaned.startswith("Javob:")
    assert "- " not in cleaned


@pytest.mark.asyncio
async def test_answer_question_degrades_without_client(registry, monkeypatch):
    monkeypatch.setattr(answer_service, "get_gemini_client", lambda settings: None)

    routed = RoutedIntent("answer_question", AnswerQuestion(query="salom"), {})
    result = await dispatch(registry, routed, now=datetime.now(UTC))
    assert "sozlanmagan" in result.text.lower()


@pytest.mark.asyncio
async def test_answer_question_remembers_conversation(registry, monkeypatch):
    """A follow-up turn feeds the previous question + answer back as context."""
    from app.services.dispatcher import clear_conversation

    owner_key = registry.settings.owner_chat_id
    clear_conversation(owner_key)
    rec = _RecordingClient("Toshkent — O'zbekiston poytaxti.")
    monkeypatch.setattr(answer_service, "get_gemini_client", lambda settings: rec)

    await dispatch(
        registry,
        RoutedIntent(
            "answer_question",
            AnswerQuestion(query="O'zbekiston poytaxti qayer?"),
            {},
        ),
        now=datetime.now(UTC),
    )
    await dispatch(
        registry,
        RoutedIntent("answer_question", AnswerQuestion(query="Aholisi qancha?"), {}),
        now=datetime.now(UTC),
    )

    # The second call must carry the first question AND its answer as context.
    second = rec.models.contents[1]
    assert "O'zbekiston poytaxti qayer?" in second
    assert "Toshkent" in second
    assert "Aholisi qancha?" in second
    clear_conversation(owner_key)


@pytest.mark.asyncio
async def test_answer_question_retries_without_grounding(registry, monkeypatch):
    """A grounding error must still produce an answer (ungrounded retry)."""
    client = _GroundingFailsClient("Web qidiruvsiz javob.")
    monkeypatch.setattr(answer_service, "get_gemini_client", lambda settings: client)
    monkeypatch.setattr(
        registry.settings, "answer_web_grounding", True, raising=False
    )

    text = await answer_service.answer_question(
        registry.settings, query="bugungi yangiliklar", needs_fresh_info=True
    )
    assert text == "Web qidiruvsiz javob."
