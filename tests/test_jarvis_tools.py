from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.brain.intent_router import INTENT_MODELS
from app.brain.intents import (
    GetChatMessages,
    GetWeather,
    JarvisBriefing,
    SearchChatMedia,
    SearchTelegramArchive,
    SummarizeChat,
)
from app.brain.nlu_schema import NLUResult
from app.services.dispatcher import dispatch


def test_jarvis_intents_are_registered():
    assert INTENT_MODELS["get_weather"] is GetWeather
    assert INTENT_MODELS["jarvis_briefing"] is JarvisBriefing
    assert INTENT_MODELS["get_chat_messages"] is GetChatMessages
    assert INTENT_MODELS["search_chat_media"] is SearchChatMedia
    assert INTENT_MODELS["summarize_chat"] is SummarizeChat
    assert INTENT_MODELS["search_telegram_archive"] is SearchTelegramArchive

    result = NLUResult(
        reasoning="Owner asks for weather.",
        intent="get_weather",
        get_weather=GetWeather(location=None, scope="today"),
    )
    assert result.get_weather is not None

    message_result = NLUResult(
        reasoning="Owner asks for the latest incoming message.",
        intent="get_chat_messages",
        get_chat_messages=GetChatMessages(contact_name="Asadbek"),
    )
    assert message_result.get_chat_messages is not None

    archive_result = NLUResult(
        reasoning="Owner searches all Telegram archive.",
        intent="search_telegram_archive",
        search_telegram_archive=SearchTelegramArchive(
            query="to'yga taklif qilgan ovozli xabar",
            chat_name=None,
            chat_types="all",
            media_type="voice",
            scope="recent",
            limit=3,
        ),
    )
    assert archive_result.search_telegram_archive is not None


@pytest.mark.asyncio
async def test_get_weather_dispatch_uses_default_location(registry, monkeypatch):
    from app.brain.intent_router import RoutedIntent

    calls = {}

    async def fake_weather(location, scope):
        calls["location"] = location
        calls["scope"] = scope
        return type("Report", (), {"text": "Toshkent: quyoshli"})()

    monkeypatch.setattr("app.services.weather_service.get_weather", fake_weather)
    result = await dispatch(
        registry,
        RoutedIntent("get_weather", GetWeather(location=None, scope="today"), {}),
        now=datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert calls == {"location": "Tashkent", "scope": "today"}
    assert "Toshkent" in result.text


@pytest.mark.asyncio
async def test_summarize_chat_requires_known_contact(registry):
    from app.brain.intent_router import RoutedIntent

    result = await dispatch(
        registry,
        RoutedIntent(
            "summarize_chat",
            SummarizeChat(contact_name="Bobur", scope="recent", limit=10),
            {},
        ),
        now=datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert "topilmadi" in result.text


@pytest.mark.asyncio
async def test_chat_media_disambiguation_has_buttons_and_resumes(registry, monkeypatch):
    from app.brain.intent_router import RoutedIntent
    from app.repositories import person_repo
    from app.services import dispatcher

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=901, display_name="Asadbek", username="asadbek"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=902, display_name="Асадбек Ишчи"
        )

    owner_key = registry.settings.owner_chat_id
    result = await dispatch(
        registry,
        RoutedIntent(
            "search_chat_media",
            SearchChatMedia(contact_name="Asadbek", media_type="photo"),
            {},
        ),
        now=datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert dispatcher.has_pending(owner_key)
    assert result.reply_markup is not None
    assert "1." in result.text and "2." in result.text

    resumed = await dispatcher.resume_choice(
        registry, 1, now=datetime(2026, 6, 25, tzinfo=UTC)
    )
    assert resumed is not None
    assert "Bot ulanishi topilmadi" in resumed.text
    assert not dispatcher.has_pending(owner_key)


@pytest.mark.asyncio
async def test_get_chat_messages_uses_disambiguated_contact(registry, monkeypatch):
    from app.brain.intent_router import RoutedIntent
    from app.repositories import person_repo
    from app.services import dispatcher
    from app.services.telegram_chat_service import ChatItem

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=911, display_name="Asadbek", username="asadbek"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=912, display_name="Асадбек"
        )

    async def fake_fetch_recent_items(registry, chat_id, *, scope, limit, direction):
        assert chat_id == 911
        assert direction == "incoming"
        return [ChatItem(sender="Kontakt", text="Salom", sent_at=None, kind="text")]

    monkeypatch.setattr(
        "app.services.telegram_chat_service.fetch_recent_items", fake_fetch_recent_items
    )

    await dispatch(
        registry,
        RoutedIntent(
            "get_chat_messages",
            GetChatMessages(contact_name="Asadbek", direction="incoming", limit=1),
            {},
        ),
        now=datetime(2026, 6, 25, tzinfo=UTC),
    )
    result = await dispatcher.resume_choice(
        registry, 1, now=datetime(2026, 6, 25, tzinfo=UTC)
    )
    assert result is not None
    assert "Salom" in result.text
