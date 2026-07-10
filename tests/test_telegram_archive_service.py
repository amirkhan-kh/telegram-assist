from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.repositories import telegram_archive_repo
from app.services.telegram_archive_indexer import run_archive_index_cycle
from app.services.telegram_archive_service import (
    _message_media_kind,
    _name_matches,
    search_archive,
)


class _Entity:
    def __init__(
        self, title: str, *, megagroup: bool = True, broadcast: bool = False
    ) -> None:
        self.title = title
        self.megagroup = megagroup
        self.broadcast = broadcast


class _PrivateEntity:
    first_name = "Bobur"
    last_name = ""
    username = "bobur"


class _Sender:
    first_name = "Ali"
    last_name = "Valiyev"
    username = "ali"


class _Message:
    def __init__(self, text: str, mid: int = 1) -> None:
        self.id = mid
        self.message = text
        self.date = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        self.chat_id = 1001
        self.out = False

    async def get_sender(self):
        return _Sender()


class _Dialog:
    def __init__(self, name: str, entity=None) -> None:
        self.name = name
        self.entity = entity or _Entity(name)


class _FakeUserbot:
    def __init__(self) -> None:
        self.message_limits: list[int | None] = []

    async def iter_dialogs(self, limit: int):
        yield _Dialog("Do'kondagilar 2025")

    async def iter_messages(self, entity, limit: int | None):
        self.message_limits.append(limit)
        yield _Message("Bugun to'y haqida xabar keldi, kechqurun boramiz.")

    async def download_media(self, message, file: str):
        raise AssertionError("text search must not download media")


@pytest.mark.asyncio
async def test_archive_search_finds_group_text(registry):
    userbot = _FakeUserbot()
    registry.userbot = userbot

    results = await search_archive(
        registry,
        query="to'y haqida xabar",
        chat_name="Do'kondagilar 2025",
        chat_types="groups",
        media_type="text",
        scope="recent",
        limit=3,
    )

    assert len(results) == 1
    assert results[0].chat_title == "Do'kondagilar 2025"
    assert results[0].sender == "Ali Valiyev (@ali)"
    assert "to'y" in results[0].text
    assert userbot.message_limits == [1000]


@pytest.mark.asyncio
async def test_archive_uses_channel_and_private_depth_limits(registry):
    class _LimitUserbot:
        def __init__(self, dialog: _Dialog) -> None:
            self.dialog = dialog
            self.message_limits: list[int | None] = []

        async def iter_dialogs(self, limit: int):
            yield self.dialog

        async def iter_messages(self, entity, limit: int | None):
            self.message_limits.append(limit)
            yield _Message("Zakaz haqida muhim xabar.")

        async def download_media(self, message, file: str):
            raise AssertionError("text search must not download media")

    channel_userbot = _LimitUserbot(
        _Dialog("Savdo kanali", _Entity("Savdo kanali", megagroup=False, broadcast=True))
    )
    registry.userbot = channel_userbot
    await search_archive(
        registry,
        query="zakaz haqida",
        chat_name="Savdo kanali",
        chat_types="channels",
        media_type="text",
        scope="recent",
        limit=1,
    )
    assert channel_userbot.message_limits == [2000]

    private_userbot = _LimitUserbot(_Dialog("Bobur", _PrivateEntity()))
    registry.userbot = private_userbot
    await search_archive(
        registry,
        query="zakaz haqida",
        chat_name="Bobur",
        chat_types="private",
        media_type="text",
        scope="recent",
        limit=1,
    )
    assert private_userbot.message_limits == [None]


def test_archive_group_name_fuzzy_matches_stt_mishear():
    assert _name_matches("Doyandagilar 2025 kanalidan", "Do'kondagilar 2025")
    assert _name_matches("Do'kondagilar 2025 gruppasidan", "Do'kondagilar 2025")


@pytest.mark.asyncio
async def test_archive_search_uses_local_index_first(registry):
    class _NoLiveScanUserbot:
        async def iter_dialogs(self, limit: int):  # pragma: no cover - must not run
            raise AssertionError("index hit must not scan Telegram dialogs")

    async with registry.session() as session:
        await telegram_archive_repo.upsert_dialog(
            session,
            dialog_id=777,
            title="Do'kondagilar 2025",
            kind="group",
            username=None,
            indexed_at=datetime.now(UTC),
        )
        await telegram_archive_repo.upsert_message(
            session,
            dialog_id=777,
            message_id=42,
            chat_title="Do'kondagilar 2025",
            chat_kind="group",
            sender_id=123,
            sender_label="Ali Valiyev",
            sent_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
            text="Bugun to'y haqida xabar keldi.",
            media_kind="text",
            has_media=False,
            out=False,
        )

    registry.userbot = _NoLiveScanUserbot()
    results = await search_archive(
        registry,
        query="to'y haqida xabar",
        chat_name="Do'kondagilar 2025",
        chat_types="groups",
        media_type="text",
        scope="recent",
        limit=3,
    )

    assert len(results) == 1
    assert results[0].dialog_id == 777
    assert results[0].message_id == 42
    assert results[0].sender == "Ali Valiyev"


@pytest.mark.asyncio
async def test_archive_indexer_stores_recent_dialog_messages(registry):
    class _DialogWithId(_Dialog):
        id = 888

    class _IndexUserbot:
        async def iter_dialogs(self, limit: int):
            yield _DialogWithId("Savdo guruhi")

        async def iter_messages(self, entity, **kwargs):
            yield _Message("Zakaz tayyor bo'ldi.", mid=9)

    await run_archive_index_cycle(registry, _IndexUserbot())

    async with registry.session() as session:
        rows = await telegram_archive_repo.search_messages(
            session,
            tokens={"zakaz"},
            chat_kinds={"group"},
            media_kinds={"text"},
            since=None,
            candidate_limit=10,
        )

    assert len(rows) == 1
    assert rows[0].dialog_id == 888
    assert rows[0].text == "Zakaz tayyor bo'ldi."


def test_archive_mp4_document_is_video():
    class _File:
        mime_type = "video/mp4"

    class _DocumentMessage:
        document = object()
        file = _File()

    assert _message_media_kind(_DocumentMessage()) == "video"
