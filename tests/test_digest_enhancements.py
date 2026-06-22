"""Tests for digest enhancements: semantic dedup (Gemini) + history backfill."""

from __future__ import annotations

from datetime import timedelta

from app.db.base import utcnow
from app.repositories import channel_repo


# ── fake Gemini client returning a fixed topic grouping ───────────────────────
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


# ── semantic dedup ────────────────────────────────────────────────────────────
async def test_semantic_dedup_merges_same_topic(registry, monkeypatch):
    now = utcnow()
    async with registry.session() as session:
        ch = await channel_repo.create(
            session, tg_channel_id=800001, username="news", title="News", weight=1.0
        )
        # Two posts, SAME topic but DIFFERENT wording (text-dedup won't catch them).
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=1, posted_at=now,
            text="Prezident bugun nutq so'zladi", views=5000, forwards=0, reactions_count=0,
        )
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=2, posted_at=now,
            text="Davlat rahbari murojaat qildi", views=1000, forwards=0, reactions_count=0,
        )
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=3, posted_at=now,
            text="Futbol o'yini natijasi", views=2000, forwards=0, reactions_count=0,
        )

    # Ranked by score: idx0=Prezident(5000), idx1=Futbol(2000), idx2=Davlat(1000).
    # Group idx0 & idx2 as the same topic -> the lower-scored idx2 is dropped.
    grouping = (
        '{"items":[{"index":0,"topic":1},{"index":1,"topic":2},'
        '{"index":2,"topic":1}]}'
    )
    monkeypatch.setattr(
        "app.services.digest_service.get_gemini_client",
        lambda _s: _fake_gemini(grouping),
    )

    # Semantic dedup runs on the scheduled (deliver=True) digest only.
    summary = await registry.digest_service.run(top_n=5, deliver=True)
    assert summary is not None
    assert "Prezident" in summary          # kept (highest score in topic)
    assert "Futbol" in summary             # distinct topic
    assert "Davlat rahbari" not in summary  # merged away by meaning


async def test_on_demand_digest_skips_llm(registry, monkeypatch):
    now = utcnow()
    async with registry.session() as session:
        ch = await channel_repo.create(
            session, tg_channel_id=800003, username="c3", title="C3", weight=1.0
        )
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=1, posted_at=now,
            text="post bir", views=10, forwards=0, reactions_count=0,
        )

    # If the on-demand digest called the LLM, this would raise -> it must not.
    def _boom(_s):
        raise AssertionError("on-demand digest must not call the LLM")

    monkeypatch.setattr("app.services.digest_service.get_gemini_client", _boom)
    summary = await registry.digest_service.run(top_n=5, deliver=False)
    assert "post bir" in summary


async def test_semantic_dedup_skipped_without_client(registry, monkeypatch):
    now = utcnow()
    async with registry.session() as session:
        ch = await channel_repo.create(
            session, tg_channel_id=800002, username="c2", title="C2", weight=1.0
        )
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=1, posted_at=now,
            text="post bir", views=10, forwards=0, reactions_count=0,
        )
        await channel_repo.upsert_post(
            session, channel_id=ch.id, tg_message_id=2, posted_at=now,
            text="post ikki", views=5, forwards=0, reactions_count=0,
        )
    # No Gemini client -> degrade to text-dedup (both distinct posts kept).
    monkeypatch.setattr(
        "app.services.digest_service.get_gemini_client", lambda _s: None
    )
    summary = await registry.digest_service.run(top_n=5, deliver=True)
    assert "post bir" in summary and "post ikki" in summary


# ── backfill ──────────────────────────────────────────────────────────────────
class _Entity:
    def __init__(self, cid, username, title, broadcast):
        self.id = cid
        self.username = username
        self.title = title
        self.broadcast = broadcast


class _Dialog:
    def __init__(self, entity):
        self.entity = entity


class _Msg:
    def __init__(self, mid, text, date):
        self.id = mid
        self.message = text
        self.date = date
        self.views = 100
        self.forwards = 1
        self.reactions = None


class _FakeTelethon:
    def __init__(self, dialogs, messages):
        self._dialogs = dialogs
        self._messages = messages

    async def iter_dialogs(self, limit=200):
        for d in self._dialogs:
            yield d

    async def iter_messages(self, entity, limit=30):
        for m in self._messages:
            yield m


async def test_backfill_ingests_only_broadcast_channels(registry):
    from app.userbot.handlers import backfill_recent_posts

    now = utcnow()
    channel = _Entity(700001, "test_ch", "Test", broadcast=True)
    group = _Entity(700002, None, "Group", broadcast=False)  # must be skipped
    messages = [
        _Msg(1, "post bir", now - timedelta(hours=1)),
        _Msg(2, "post ikki", now - timedelta(hours=2)),
    ]
    client = _FakeTelethon([_Dialog(channel), _Dialog(group)], messages)

    count = await backfill_recent_posts(client, registry, per_channel=10, max_channels=10)
    assert count == 2  # only the broadcast channel's two messages

    async with registry.session() as session:
        posts = await channel_repo.posts_since(
            session, now - timedelta(days=1), only_undigested=True
        )
    assert {p.tg_message_id for p in posts} == {1, 2}


def test_digest_enhancement_config_defaults(settings):
    assert settings.digest_semantic_dedup is True
    assert settings.digest_backfill_per_channel == 30
    assert settings.digest_backfill_max_channels == 40
