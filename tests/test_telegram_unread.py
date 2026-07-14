"""Telegram unread digest — fetch/cap logic + morning-plan block rendering."""

from __future__ import annotations

from app.services.briefing_service import BriefingService
from app.services.telegram_unread_service import (
    UnreadChat,
    UnreadSummary,
    fetch_unread_summary,
)


class _FakeDialog:
    def __init__(
        self,
        name,
        unread_count,
        *,
        is_user=False,
        is_group=False,
        is_channel=False,
        bot=False,
        archived=False,
    ):
        self.name = name
        self.unread_count = unread_count
        self.is_user = is_user
        self.is_group = is_group
        self.is_channel = is_channel
        self.archived = archived
        self.entity = type("E", (), {"bot": bot})()


class _FakeClient:
    def __init__(self, dialogs):
        self._dialogs = dialogs

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def iter_dialogs(self, limit=200):
        for d in self._dialogs[:limit]:
            yield d


async def test_fetch_unread_summary_groups_and_caps(registry):
    registry.userbot = _FakeClient(
        [
            _FakeDialog("Aziz", 3, is_user=True),
            _FakeDialog("Bek", 1, is_user=True),
            _FakeDialog("NotifBot", 9, is_user=True, bot=True),  # skipped (bot)
            _FakeDialog("Read chat", 0, is_user=True),  # skipped (no unread)
            _FakeDialog("Loyiha guruhi", 5, is_group=True),
            _FakeDialog("Tech kanal", 2, is_channel=True),
            _FakeDialog("Arxiv", 4, is_user=True, archived=True),  # skipped
        ]
    )
    registry.settings.telegram_unread_max_dms = 1  # cap DMs to force a hidden one

    summary = await fetch_unread_summary(registry)
    assert summary is not None
    # DMs sorted by unread desc, capped to 1 -> only Aziz kept, Bek hidden.
    assert [c.name for c in summary.dms] == ["Aziz"]
    assert [c.name for c in summary.groups] == ["Loyiha guruhi"]
    assert [c.name for c in summary.channels] == ["Tech kanal"]
    assert summary.hidden_chats == 1  # Bek dropped by the cap
    # Total counts every unread person/group/channel (bot + archived excluded).
    assert summary.total_unread == 3 + 1 + 5 + 2


async def test_fetch_unread_summary_none_without_userbot(registry):
    registry.userbot = None
    assert await fetch_unread_summary(registry) is None


def test_telegram_block_renders_counts(registry):
    summary = UnreadSummary(
        dms=[UnreadChat("Aziz", 3, "dm")],
        groups=[UnreadChat("Loyiha", 5, "group")],
        channels=[UnreadChat("Kanal", 2, "channel")],
        total_unread=10,
        hidden_chats=0,
    )
    block = BriefingService(registry)._telegram_block(summary)
    assert "Telegram — 10 o'qilmagan xabar" in block
    assert "👤 <b>Aziz</b> (3)" in block
    assert "👥 Loyiha (5)" in block
    assert "📢 Kanal (2)" in block
