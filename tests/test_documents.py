"""Tests for the document-photo flow: vision extraction, expiry event, storage."""

from __future__ import annotations

from datetime import date

from app.repositories import document_repo, person_repo
from app.services.document_service import DocumentService, _parse_iso_date


# ── fake Gemini client (mirrors the digest-test stand-in) ─────────────────────
class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate_content(self, *, model, contents, config):
        return _FakeResp(self._text)


def _fake_gemini(json_text: str):
    client = type("C", (), {})()
    client.aio = type("Aio", (), {"models": _FakeModels(json_text)})()
    return client


# ── date parsing ──────────────────────────────────────────────────────────────
def test_parse_iso_date():
    assert _parse_iso_date("2027-06-10") == date(2027, 6, 10)
    assert _parse_iso_date(None) is None
    assert _parse_iso_date("10.06.2027") is None  # only strict ISO is accepted
    assert _parse_iso_date("2027-13-40") is None  # invalid month/day


# ── vision extraction ─────────────────────────────────────────────────────────
async def test_extract_expiry_reads_date(registry, monkeypatch):
    monkeypatch.setattr(
        "app.services.document_service.get_gemini_client",
        lambda _s: _fake_gemini('{"expiry": "2027-06-10"}'),
    )
    got = await DocumentService(registry).extract_expiry(b"img", "image/jpeg")
    assert got == date(2027, 6, 10)


async def test_extract_expiry_null_is_none(registry, monkeypatch):
    monkeypatch.setattr(
        "app.services.document_service.get_gemini_client",
        lambda _s: _fake_gemini('{"expiry": null}'),
    )
    assert await DocumentService(registry).extract_expiry(b"img") is None


async def test_extract_expiry_no_client_is_none(registry, monkeypatch):
    monkeypatch.setattr(
        "app.services.document_service.get_gemini_client", lambda _s: None
    )
    assert await DocumentService(registry).extract_expiry(b"img") is None


# ── expiry event (7/3/1-day alerts, no offset, non-yearly) ────────────────────
async def test_add_document_event_uses_expiry_directly(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_document_event(
        owner_id=owner.id, kind="passport", expiry=date(2030, 6, 10)
    )
    assert event.event_date == date(2030, 6, 10)  # exact expiry, no offset
    # Stored sorted ascending; the alerts still fire 7, 3 and 1 days before.
    assert event.remind_days_before == [1, 3, 7]
    assert event.yearly is False


# ── storage + retrieval ───────────────────────────────────────────────────────
async def test_document_repo_keeps_latest_per_kind(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        await document_repo.create(
            session, owner_id=owner.id, kind="passport", file_id="file_A"
        )
        await document_repo.create(
            session, owner_id=owner.id, kind="passport", file_id="file_B"
        )
        await document_repo.create(
            session, owner_id=owner.id, kind="insurance", file_id="file_C"
        )
    async with registry.session() as session:
        latest = await document_repo.latest_by_kind(session, owner.id, "passport")
        gallery = await document_repo.list_latest_per_kind(session, owner.id)
    assert latest.file_id == "file_B"  # the newer passport photo wins
    # One row per kind: newest passport + the insurance.
    assert {(p.kind, p.file_id) for p in gallery} == {
        ("passport", "file_B"),
        ("insurance", "file_C"),
    }


async def test_delete_by_kind_removes_photos_and_returns_event_ids(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        photo = await document_repo.create(
            session, owner_id=owner.id, kind="passport", file_id="old"
        )
        photo_id = photo.id
    event = await registry.event_service.add_document_event(
        owner_id=owner.id, kind="passport", expiry=date(2030, 1, 1)
    )
    async with registry.session() as session:
        await document_repo.set_event_id(session, photo_id, event.id)
    async with registry.session() as session:
        removed = await document_repo.delete_by_kind(session, owner.id, "passport")
    assert removed == [event.id]  # caller cancels these events
    async with registry.session() as session:
        gone = await document_repo.latest_by_kind(session, owner.id, "passport")
    assert gone is None


async def test_document_repo_links_event(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        photo = await document_repo.create(
            session, owner_id=owner.id, kind="inspection", file_id="file_X"
        )
        photo_id = photo.id
    event = await registry.event_service.add_document_event(
        owner_id=owner.id, kind="inspection", expiry=date(2029, 1, 1)
    )
    async with registry.session() as session:
        await document_repo.set_event_id(session, photo_id, event.id)
    async with registry.session() as session:
        linked = await document_repo.latest_by_kind(session, owner.id, "inspection")
    assert linked.event_id == event.id
