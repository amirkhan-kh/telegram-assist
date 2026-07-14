"""Telegram unread digest — a compact "who pinged you" view for the morning plan.

Read live from the owner's Telethon userbot, the owner's unread dialogs are
grouped into direct messages, groups and channels, each sorted by unread volume
and capped, so the briefing can show *who* is waiting (contact name + unread
count) without dumping every noisy channel. Degrades to ``None`` whenever the
userbot is not connected/authorized, so the section is simply skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class UnreadChat:
    """One unread dialog: display name, unread message count, and kind."""

    name: str
    count: int
    kind: str  # 'dm' | 'group' | 'channel'


@dataclass
class UnreadSummary:
    """Capped, per-kind unread digest plus the true total unread volume."""

    dms: list[UnreadChat] = field(default_factory=list)
    groups: list[UnreadChat] = field(default_factory=list)
    channels: list[UnreadChat] = field(default_factory=list)
    total_unread: int = 0  # unread messages across *all* unread chats (pre-cap)
    hidden_chats: int = 0  # unread chats dropped by the per-category caps

    @property
    def is_empty(self) -> bool:
        return not (self.dms or self.groups or self.channels)


def _dialog_kind(dialog: Any) -> str:
    """Map a Telethon dialog to 'dm' | 'group' | 'channel'.

    Broadcast channels are ``is_channel`` and not ``is_group``; supergroups and
    basic groups are ``is_group`` (supergroups are also ``is_channel``), so the
    group check must come before the channel check.
    """
    if getattr(dialog, "is_user", False):
        return "dm"
    if getattr(dialog, "is_group", False):
        return "group"
    if getattr(dialog, "is_channel", False):
        return "channel"
    return "group"


async def fetch_unread_summary(registry: ServiceRegistry) -> UnreadSummary | None:
    """Read the owner's unread dialogs live.

    Returns ``None`` when the userbot is absent or not authorized (or on any read
    error), so the caller can skip the section without a crash.
    """
    client = registry.userbot
    if client is None:
        return None
    try:
        if not client.is_connected() or not await client.is_user_authorized():
            return None
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning("telegram.unread.auth_check_failed", error=str(exc)[:120])
        return None

    settings = registry.settings
    scan_limit = max(1, settings.telegram_unread_scan_limit)
    dms: list[UnreadChat] = []
    groups: list[UnreadChat] = []
    channels: list[UnreadChat] = []
    try:
        async for dialog in client.iter_dialogs(limit=scan_limit):
            if getattr(dialog, "archived", False):
                continue
            count = int(getattr(dialog, "unread_count", 0) or 0)
            if count <= 0:
                continue
            kind = _dialog_kind(dialog)
            entity = getattr(dialog, "entity", None)
            if kind == "dm" and getattr(entity, "bot", False):
                continue  # skip bot chats — notifications, not people
            name = (getattr(dialog, "name", None) or "Nomsiz").strip() or "Nomsiz"
            chat = UnreadChat(name=name, count=count, kind=kind)
            if kind == "dm":
                dms.append(chat)
            elif kind == "group":
                groups.append(chat)
            else:
                channels.append(chat)
    except Exception as exc:  # noqa: BLE001 - a failed read must not break the plan
        logger.warning("telegram.unread.iter_failed", error=str(exc)[:160])
        return None

    for bucket in (dms, groups, channels):
        bucket.sort(key=lambda c: c.count, reverse=True)

    total_unread = sum(c.count for c in dms + groups + channels)
    kept_dms = dms[: settings.telegram_unread_max_dms]
    kept_groups = groups[: settings.telegram_unread_max_groups]
    kept_channels = channels[: settings.telegram_unread_max_channels]
    hidden = (
        (len(dms) - len(kept_dms))
        + (len(groups) - len(kept_groups))
        + (len(channels) - len(kept_channels))
    )
    summary = UnreadSummary(
        dms=kept_dms,
        groups=kept_groups,
        channels=kept_channels,
        total_unread=total_unread,
        hidden_chats=hidden,
    )
    return None if summary.is_empty else summary
