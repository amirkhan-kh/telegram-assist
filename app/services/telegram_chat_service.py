"""Telegram private-chat intelligence via the owner's Telethon userbot."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    sent_at: datetime | None


@dataclass(frozen=True)
class MediaResult:
    path: str
    caption: str
    kind: str


@dataclass(frozen=True)
class ChatItem:
    sender: str
    text: str
    sent_at: datetime | None
    kind: str
    path: str | None = None


def _scope_since(scope: str, *, tz: str) -> datetime | None:
    now = datetime.now(ZoneInfo(tz))
    if scope == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == "week":
        return now - timedelta(days=7)
    return None


def _matches_direction(message: Any, direction: str) -> bool:
    outgoing = bool(getattr(message, "out", False))
    if direction == "incoming":
        return not outgoing
    if direction == "outgoing":
        return outgoing
    return True


def _matches_media(message: Any, media_type: str) -> bool:
    kind = _message_media_kind(message)
    if media_type == "any":
        return kind != "text"
    if media_type == "document":
        return kind in {"document", "media"}
    if media_type == "video":
        return kind == "video"
    if media_type == "photo":
        return kind == "photo"
    return False


async def fetch_messages(
    registry: ServiceRegistry,
    chat_id: int,
    *,
    scope: str,
    limit: int,
    direction: str = "both",
) -> list[ChatMessage]:
    """Read recent text messages from a private chat."""
    client = registry.userbot
    if client is None:
        raise RuntimeError("Userbot ulanmagan.")
    since = _scope_since(scope, tz=registry.settings.user_timezone)
    rows: list[ChatMessage] = []
    async for message in client.iter_messages(chat_id, limit=max(1, min(limit, 1000))):
        if not _matches_direction(message, direction):
            continue
        sent_at = getattr(message, "date", None)
        if since is not None and sent_at is not None and sent_at < since:
            continue
        text = (getattr(message, "message", None) or "").strip()
        if not text:
            continue
        rows.append(
            ChatMessage(
                sender="Siz" if getattr(message, "out", False) else "Kontakt",
                text=text,
                sent_at=sent_at,
            )
        )
    return list(reversed(rows))


async def fetch_recent_items(
    registry: ServiceRegistry,
    chat_id: int,
    *,
    scope: str,
    limit: int,
    direction: str,
) -> list[ChatItem]:
    """Read recent messages of any useful type, downloading media when present."""
    client = registry.userbot
    if client is None:
        raise RuntimeError("Userbot ulanmagan.")
    since = _scope_since(scope, tz=registry.settings.user_timezone)
    target = Path(tempfile.gettempdir()) / "telegram_assistant_media"
    target.mkdir(parents=True, exist_ok=True)
    rows: list[ChatItem] = []
    async for message in client.iter_messages(chat_id, limit=1000):
        if not _matches_direction(message, direction):
            continue
        sent_at = getattr(message, "date", None)
        if since is not None and sent_at is not None and sent_at < since:
            continue
        text = (getattr(message, "message", None) or "").strip()
        path: str | None = None
        kind = "text"
        if getattr(message, "media", None):
            try:
                downloaded = await client.download_media(message, file=str(target))
                path = str(downloaded) if downloaded else None
            except Exception as exc:  # noqa: BLE001 - keep scanning older messages
                logger.warning("userbot.chat.download_failed", error=str(exc))
                path = None
            kind = _kind_from_path(path, "document") if path else "media"
        elif not text:
            continue
        rows.append(
            ChatItem(
                sender="Siz" if getattr(message, "out", False) else "Kontakt",
                text=text,
                sent_at=sent_at,
                kind=kind,
                path=path,
            )
        )
        if len(rows) >= max(1, min(limit, 20)):
            break
    return list(reversed(rows))


async def fetch_media(
    registry: ServiceRegistry,
    chat_id: int,
    *,
    media_type: str,
    direction: str,
    limit: int,
) -> list[MediaResult]:
    """Download matching chat media to temporary files."""
    client = registry.userbot
    if client is None:
        raise RuntimeError("Userbot ulanmagan.")
    target = Path(tempfile.gettempdir()) / "telegram_assistant_media"
    target.mkdir(parents=True, exist_ok=True)
    found: list[MediaResult] = []
    async for message in client.iter_messages(chat_id, limit=300):
        if not _matches_direction(message, direction):
            continue
        if not _matches_media(message, media_type):
            continue
        path = await client.download_media(message, file=str(target))
        if not path:
            continue
        found.append(
            MediaResult(
                path=str(path),
                caption=(getattr(message, "message", None) or "").strip(),
                kind=_kind_from_path(str(path), media_type),
            )
        )
        if len(found) >= max(1, min(limit, registry.settings.jarvis_chat_media_limit)):
            break
    return found


async def cleanup_media(items: list[MediaResult]) -> None:
    """Best-effort cleanup after the bot has uploaded downloaded files."""
    for item in items:
        try:
            await asyncio.to_thread(os.remove, item.path)
        except OSError:
            pass


async def cleanup_item_paths(items: list[ChatItem]) -> None:
    """Best-effort cleanup for downloaded chat item media."""
    for item in items:
        if not item.path:
            continue
        try:
            await asyncio.to_thread(os.remove, item.path)
        except OSError:
            pass


def _kind_from_path(path: str, fallback: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return "photo"
    if ext in {".mp4", ".mov", ".mkv", ".webm"}:
        return "video"
    return fallback if fallback != "any" else "document"


def _message_media_kind(message: Any) -> str:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None) or getattr(message, "video_note", None):
        return "video"
    if getattr(message, "document", None):
        mime = _message_mime_type(message)
        if mime.startswith("video/"):
            return "video"
        return "document"
    if getattr(message, "media", None):
        return "media"
    return "text"


def _message_mime_type(message: Any) -> str:
    file_obj = getattr(message, "file", None)
    mime = (getattr(file_obj, "mime_type", None) or "").strip().lower()
    if mime:
        return mime
    document = getattr(message, "document", None)
    return (getattr(document, "mime_type", None) or "").strip().lower()
