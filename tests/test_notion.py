"""Notion tests — availability, DB-id caching, decision auto-archive, save note.

No network: ``NotionService._post`` is monkeypatched to a recorder that returns
canned object ids, so the create-database/create-page flow is exercised offline.
"""

from __future__ import annotations

from app.brain.intent_router import RoutedIntent
from app.brain.intents import LogDecision, SaveToNotion
from app.db.base import utcnow
from app.repositories import setting_repo
from app.services.dispatcher import dispatch
from app.services.notion_service import NotionService


def _enable_notion(registry, monkeypatch):
    monkeypatch.setattr(registry.settings, "notion_api_key", "secret_tok")
    monkeypatch.setattr(registry.settings, "notion_parent_page_id", "parent_page")


async def test_notion_unavailable_without_config(registry):
    svc = NotionService(registry)
    assert svc.available() is False


async def test_archive_decision_creates_db_then_caches(registry, monkeypatch):
    _enable_notion(registry, monkeypatch)
    svc = NotionService(registry)
    assert svc.available() is True

    calls: list[tuple[str, dict]] = []

    async def _fake_post(path: str, body: dict) -> dict:
        calls.append((path, body))
        return {"id": f"obj{len(calls)}"}

    monkeypatch.setattr(svc, "_post", _fake_post)

    ok = await svc.archive_decision(text="qaror bir", tag="loyiha", decided_at=utcnow())
    assert ok is True
    # First the database is created, then the page (row).
    assert calls[0][0] == "/databases"
    assert calls[1][0] == "/pages"
    # The created db id is cached in settings.
    async with registry.session() as session:
        cached = await setting_repo.get_value(session, "notion_db_decisions")
    assert cached == "obj1"

    # A second decision reuses the cached db -> no new /databases call.
    calls.clear()
    await svc.archive_decision(text="qaror ikki", tag=None, decided_at=utcnow())
    assert all(path != "/databases" for path, _ in calls)
    assert calls[0][0] == "/pages"


async def test_decision_dispatch_auto_archives(registry, monkeypatch):
    _enable_notion(registry, monkeypatch)
    svc = NotionService(registry)
    registry.notion_service = svc

    archived: list[dict] = []

    async def _fake_archive(**kwargs):
        archived.append(kwargs)
        return True

    monkeypatch.setattr(svc, "archive_decision", _fake_archive)

    result = await dispatch(
        registry,
        RoutedIntent("log_decision", LogDecision(text="Yangi loyiha"), {}),
        now=utcnow(),
    )
    assert "jurnalga yozildi" in result.text
    assert archived and archived[0]["text"] == "Yangi loyiha"


async def test_save_to_notion_dispatch(registry, monkeypatch):
    _enable_notion(registry, monkeypatch)
    svc = NotionService(registry)
    registry.notion_service = svc

    saved: list[dict] = []

    async def _fake_save(**kwargs):
        saved.append(kwargs)
        return True

    monkeypatch.setattr(svc, "save_note", _fake_save)

    result = await dispatch(
        registry,
        RoutedIntent("save_to_notion", SaveToNotion(text="Reja: Q3 strategiya"), {}),
        now=utcnow(),
    )
    assert "saqlandi" in result.text
    assert saved and "Q3 strategiya" in saved[0]["text"]


async def test_save_to_notion_not_configured(registry):
    registry.notion_service = None
    result = await dispatch(
        registry,
        RoutedIntent("save_to_notion", SaveToNotion(text="x"), {}),
        now=utcnow(),
    )
    assert "Notion ulanmagan" in result.text
