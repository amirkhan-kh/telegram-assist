"""Decisions-journal tests — log, list and undo via the dispatcher/service."""

from __future__ import annotations

from app.brain.intent_router import RoutedIntent
from app.brain.intents import ListDecisions, LogDecision
from app.db.base import utcnow
from app.repositories import person_repo
from app.services.dispatcher import dispatch


async def test_log_and_list_decision(registry):
    routed = RoutedIntent(
        "log_decision",
        LogDecision(text="Iyuldan yangi loyiha boshlaymiz", tag="loyiha"),
        {},
    )
    result = await dispatch(registry, routed, now=utcnow())
    assert "jurnalga yozildi" in result.text

    listed = await dispatch(
        registry, RoutedIntent("list_decisions", ListDecisions(), {}), now=utcnow()
    )
    assert "Iyuldan yangi loyiha" in listed.text
    assert "loyiha" in listed.text  # tag rendered


async def test_empty_decision_rejected(registry):
    routed = RoutedIntent("log_decision", LogDecision(text="   "), {})
    result = await dispatch(registry, routed, now=utcnow())
    assert "bo'sh" in result.text.lower()


async def test_decision_delete(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    decision = await registry.decision_service.add(
        owner_id=owner.id, text="Test qaror"
    )
    assert await registry.decision_service.delete(decision.id) is True
    remaining = await registry.decision_service.list_recent(owner.id)
    assert all(d.id != decision.id for d in remaining)
