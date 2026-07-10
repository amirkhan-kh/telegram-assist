"""Background indexer for the owner's visible Telegram archive."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.repositories import telegram_archive_repo
from app.services.telegram_archive_service import (
    _dialog_kind,
    _dialog_title,
    _message_media_kind,
    _sender_label,
)

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


async def run_archive_index_cycle(registry: ServiceRegistry, client: Any) -> int:
    """Index a bounded slice of dialogs/messages.

    The function is intentionally best-effort and bounded by settings so it can
    run in the background without delaying normal bot actions.
    """
    settings = registry.settings
    if not settings.jarvis_archive_index_enabled:
        return 0

    dialog_limit = max(1, settings.jarvis_archive_index_dialog_limit)
    per_dialog = max(1, settings.jarvis_archive_index_messages_per_dialog)
    max_messages = max(1, settings.jarvis_archive_index_max_messages_per_run)
    indexed = 0
    dialogs_seen = 0
    dialogs_indexed = 0

    try:
        async for dialog in client.iter_dialogs(limit=dialog_limit):
            if indexed >= max_messages:
                break
            entity = getattr(dialog, "entity", None)
            if entity is None:
                continue
            kind = _dialog_kind(entity)
            if kind not in {"private", "group", "channel"}:
                continue
            dialogs_seen += 1
            title = _dialog_title(dialog)
            dialog_id = _dialog_id(dialog, entity)
            if dialog_id is None:
                continue
            await _upsert_dialog(registry, dialog_id, title, kind, entity)

            remaining = max_messages - indexed
            first_limit = min(per_dialog, remaining)
            count = await _index_dialog_messages(
                registry, client, dialog, limit=first_limit
            )
            indexed += count
            dialogs_indexed += 1 if count else 0
            if indexed >= max_messages:
                break

            oldest = await _oldest_cursor(registry, dialog_id)
            if oldest is None:
                continue
            fully_indexed = await _history_fully_indexed(registry, dialog_id)
            if fully_indexed:
                continue
            remaining = max_messages - indexed
            older_limit = min(per_dialog, remaining)
            older_count = await _index_dialog_messages(
                registry, client, dialog, limit=older_limit, max_id=oldest
            )
            indexed += older_count
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_archive.index_cycle_failed", error=str(exc)[:200])

    logger.info(
        "telegram_archive.index_cycle.done",
        dialogs_seen=dialogs_seen,
        dialogs_indexed=dialogs_indexed,
        messages=indexed,
    )
    return indexed


async def index_event_message(registry: ServiceRegistry, event: Any) -> None:
    """Index one live Telethon NewMessage event."""
    if not registry.settings.jarvis_archive_index_enabled:
        return
    message = getattr(event, "message", None)
    if message is None:
        return
    try:
        chat = await event.get_chat()
        kind = _dialog_kind(chat)
        if kind not in {"private", "group", "channel"}:
            return
        title = _entity_title(chat)
        dialog_id = getattr(event, "chat_id", None) or getattr(chat, "id", None)
        if dialog_id is None:
            return
        await _upsert_dialog(registry, int(dialog_id), title, kind, chat)
        fields = await _message_fields(
            dialog_id=int(dialog_id),
            chat_title=title,
            chat_kind=kind,
            message=message,
        )
        async with registry.session() as session:
            await telegram_archive_repo.upsert_message(session, **fields)
            await telegram_archive_repo.mark_dialog_indexed(
                session,
                dialog_id=int(dialog_id),
                newest_message_id=fields["message_id"],
                oldest_message_id=fields["message_id"],
                indexed_at=datetime.now(UTC),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_archive.index_event_failed", error=str(exc)[:200])


async def _index_dialog_messages(
    registry: ServiceRegistry,
    client: Any,
    dialog: Any,
    *,
    limit: int,
    max_id: int | None = None,
) -> int:
    entity = getattr(dialog, "entity", None)
    if entity is None:
        return 0
    kind = _dialog_kind(entity)
    title = _dialog_title(dialog)
    dialog_id = _dialog_id(dialog, entity)
    if dialog_id is None:
        return 0

    fields: list[dict[str, Any]] = []
    iter_kwargs: dict[str, Any] = {"limit": limit}
    if max_id is not None:
        iter_kwargs["max_id"] = max_id
    async for message in client.iter_messages(entity, **iter_kwargs):
        if message is None:
            continue
        fields.append(
            await _message_fields(
                dialog_id=dialog_id,
                chat_title=title,
                chat_kind=kind,
                message=message,
            )
        )

    if not fields:
        if max_id is not None:
            async with registry.session() as session:
                await telegram_archive_repo.mark_dialog_indexed(
                    session,
                    dialog_id=dialog_id,
                    newest_message_id=None,
                    oldest_message_id=None,
                    indexed_at=datetime.now(UTC),
                    fully_indexed=True,
                )
        return 0

    newest = max(int(item["message_id"]) for item in fields)
    oldest = min(int(item["message_id"]) for item in fields)
    fully_indexed = max_id is not None and len(fields) < limit
    if max_id is None and len(fields) < limit:
        fully_indexed = True

    async with registry.session() as session:
        for item in fields:
            await telegram_archive_repo.upsert_message(session, **item)
        await telegram_archive_repo.mark_dialog_indexed(
            session,
            dialog_id=dialog_id,
            newest_message_id=newest,
            oldest_message_id=oldest,
            indexed_at=datetime.now(UTC),
            fully_indexed=fully_indexed,
        )
    return len(fields)


async def _message_fields(
    *,
    dialog_id: int,
    chat_title: str,
    chat_kind: str,
    message: Any,
) -> dict[str, Any]:
    media_kind = _message_media_kind(message)
    sender = await _sender_label(message)
    text = (getattr(message, "message", None) or "").strip() or None
    return {
        "dialog_id": int(dialog_id),
        "message_id": int(message.id),
        "chat_title": chat_title,
        "chat_kind": chat_kind,
        "sender_id": _sender_id(message),
        "sender_label": sender,
        "sent_at": getattr(message, "date", None),
        "text": text,
        "media_kind": media_kind,
        "has_media": media_kind != "text",
        "out": bool(getattr(message, "out", False)),
    }


async def _upsert_dialog(
    registry: ServiceRegistry,
    dialog_id: int,
    title: str,
    kind: str,
    entity: Any,
) -> None:
    async with registry.session() as session:
        await telegram_archive_repo.upsert_dialog(
            session,
            dialog_id=int(dialog_id),
            title=title,
            kind=kind,
            username=getattr(entity, "username", None),
            indexed_at=datetime.now(UTC),
        )


async def _oldest_cursor(registry: ServiceRegistry, dialog_id: int) -> int | None:
    async with registry.session() as session:
        row = await telegram_archive_repo.get_dialog(session, dialog_id=dialog_id)
        return row.oldest_indexed_message_id if row is not None else None


async def _history_fully_indexed(registry: ServiceRegistry, dialog_id: int) -> bool:
    async with registry.session() as session:
        row = await telegram_archive_repo.get_dialog(session, dialog_id=dialog_id)
        return bool(row.history_fully_indexed) if row is not None else False


def _dialog_id(dialog: Any, entity: Any) -> int | None:
    value = getattr(dialog, "id", None)
    if value is None:
        value = getattr(entity, "id", None)
    return int(value) if value is not None else None


def _entity_title(entity: Any) -> str:
    return _dialog_title(SimpleNamespace(name=None, entity=entity))


def _sender_id(message: Any) -> int | None:
    value = getattr(message, "sender_id", None)
    if value is None:
        from_id = getattr(message, "from_id", None)
        value = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
    return int(value) if value is not None else None
