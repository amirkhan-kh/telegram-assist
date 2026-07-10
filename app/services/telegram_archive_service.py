"""Global Telegram archive search across private chats, groups, and channels."""

from __future__ import annotations

import asyncio
import html
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.brain.translit import normalize_name
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger
from app.repositories import setting_repo, telegram_archive_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class ArchiveSearchResult:
    chat_title: str
    chat_kind: str
    sender: str
    sent_at: datetime | None
    text: str
    media_kind: str
    path: str | None = None
    description: str | None = None
    dialog_id: int | None = None
    message_id: int | None = None


_WORD_RE = re.compile(r"[a-z0-9_ʻʼ'`]+", re.IGNORECASE)
_VISUAL_HINT_RE = re.compile(
    r"\b(video|rasm|foto|tasvir|ko['‘’ʻʼ`]?cha|shahar|yo['‘’ʻʼ`]?l|uylar|"
    r"do['‘’ʻʼ`]?kon|mashina|bino|ko['‘’ʻʼ`]?rinadi|olingan)\b",
    re.IGNORECASE,
)
_VOICE_HINT_RE = re.compile(
    r"\b(ovozli|voice|audio|gapirgan|aytgan|taklif qilgan|taklif qilgandi)\b",
    re.IGNORECASE,
)
_CHAT_STOPWORDS = {
    "group",
    "gruppa",
    "gruppasi",
    "gruppasidan",
    "guruh",
    "guruhdan",
    "kanal",
    "kanali",
    "kanalidan",
    "chat",
    "chatdan",
}


def _scope_since(scope: str, *, tz: str) -> datetime | None:
    now = datetime.now(ZoneInfo(tz))
    if scope == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == "week":
        return now - timedelta(days=7)
    return None


async def search_archive(
    registry: ServiceRegistry,
    *,
    query: str,
    chat_name: str | None,
    chat_types: str,
    media_type: str,
    scope: str,
    limit: int,
) -> list[ArchiveSearchResult]:
    """Search visible Telegram dialogs for a semantic-ish text/media match."""
    client = registry.userbot
    if client is None:
        raise RuntimeError("Userbot ulanmagan.")

    target = Path(tempfile.gettempdir()) / "telegram_assistant_archive"
    target.mkdir(parents=True, exist_ok=True)
    indexed = await _search_index(
        registry,
        query=query,
        chat_name=chat_name,
        chat_types=chat_types,
        media_type=media_type,
        scope=scope,
        limit=limit,
        target=target,
    )
    if indexed:
        logger.info(
            "telegram_archive.search.index_hit",
            query=query[:120],
            chat_name=chat_name,
            chat_types=chat_types,
            media_type=media_type,
            results=len(indexed),
        )
        return indexed

    results: list[ArchiveSearchResult] = []
    since = _scope_since(scope, tz=registry.settings.user_timezone)
    max_dialogs = max(1, registry.settings.jarvis_archive_dialog_limit)
    max_analyze = max(0, registry.settings.jarvis_archive_media_analyze_limit)
    message_limits = _message_limits(registry)
    analyzed = 0
    scanned_dialogs = 0
    matched_dialogs = 0
    scanned_messages = 0
    media_seen = 0

    async for dialog in client.iter_dialogs(limit=max_dialogs):
        scanned_dialogs += 1
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        title = _dialog_title(dialog)
        kind = _dialog_kind(entity)
        if not _chat_kind_allowed(kind, chat_types):
            continue
        if chat_name and not _name_matches(chat_name, title):
            continue
        matched_dialogs += 1
        message_limit = _message_limit_for_kind(registry, kind)
        logger.info(
            "telegram_archive.dialog.matched",
            title=title,
            kind=kind,
            message_limit="all" if message_limit is None else message_limit,
        )
        async for message in client.iter_messages(entity, limit=message_limit):
            scanned_messages += 1
            sent_at = getattr(message, "date", None)
            if since is not None and sent_at is not None and sent_at < since:
                continue
            msg_media = _message_media_kind(message)
            if not _media_allowed(msg_media, media_type):
                continue
            if msg_media != "text":
                media_seen += 1
            caption = (getattr(message, "message", None) or "").strip()
            searchable = caption
            path: str | None = None
            description: str | None = None

            direct_score = _score(query, searchable)
            should_analyze = (
                direct_score < 2
                and analyzed < max_analyze
                and msg_media in {"voice", "audio", "video", "photo"}
                and _query_wants_media_analysis(query, media_type, msg_media)
            )
            if should_analyze:
                path = await _download_media(client, message, target)
                if path:
                    analyzed += 1
                    description = await _describe_or_transcribe(
                        registry, message, path, query=query, media_kind=msg_media
                    )
                    searchable = " ".join(x for x in (caption, description or "") if x)

            if _score(query, searchable) < 2 and not _strong_match(query, searchable):
                continue
            if msg_media != "text" and path is None:
                path = await _download_media(client, message, target)
            results.append(
                ArchiveSearchResult(
                    chat_title=title,
                    chat_kind=kind,
                    sender=await _sender_label(message),
                    sent_at=sent_at,
                    text=caption,
                    media_kind=msg_media,
                    path=path,
                    description=description,
                    dialog_id=_message_dialog_id(message),
                    message_id=int(getattr(message, "id", 0) or 0) or None,
                )
            )
            if len(results) >= max(1, min(limit, 10)):
                _log_search_done(
                    query=query,
                    chat_name=chat_name,
                    chat_types=chat_types,
                    media_type=media_type,
                    scanned_dialogs=scanned_dialogs,
                    matched_dialogs=matched_dialogs,
                    scanned_messages=scanned_messages,
                    media_seen=media_seen,
                    analyzed=analyzed,
                    results=len(results),
                    message_limits=message_limits,
                )
                return results
    _log_search_done(
        query=query,
        chat_name=chat_name,
        chat_types=chat_types,
        media_type=media_type,
        scanned_dialogs=scanned_dialogs,
        matched_dialogs=matched_dialogs,
        scanned_messages=scanned_messages,
        media_seen=media_seen,
        analyzed=analyzed,
        results=len(results),
        message_limits=message_limits,
    )
    return results


async def cleanup_results(results: list[ArchiveSearchResult]) -> None:
    """Best-effort cleanup for downloaded files."""
    for item in results:
        if not item.path:
            continue
        try:
            await asyncio.to_thread(os.remove, item.path)
        except OSError:
            pass


def result_context(result: ArchiveSearchResult, *, timezone: str) -> str:
    when = ""
    if result.sent_at is not None:
        when = result.sent_at.astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M")
    bits = [
        f"📌 Chat: {result.chat_title}",
        f"👤 Yuborgan: {result.sender}",
    ]
    if when:
        bits.append(f"🕒 {when}")
    if result.text:
        bits.append(f"💬 {result.text}")
    if result.description:
        bits.append(f"🧠 {result.description}")
    return "\n".join(bits)


def html_result_context(result: ArchiveSearchResult, *, timezone: str) -> str:
    return html.escape(result_context(result, timezone=timezone), quote=False)


def _dialog_title(dialog: Any) -> str:
    name = (getattr(dialog, "name", None) or "").strip()
    if name:
        return name
    entity = getattr(dialog, "entity", None)
    title = (getattr(entity, "title", None) or "").strip()
    if title:
        return title
    first = (getattr(entity, "first_name", None) or "").strip()
    last = (getattr(entity, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    return full or (getattr(entity, "username", None) or "Noma'lum chat")


def _dialog_kind(entity: Any) -> str:
    if hasattr(entity, "first_name") or hasattr(entity, "last_name"):
        return "private"
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
        return "group"
    title = getattr(entity, "title", None)
    return "group" if title else "chat"


def _chat_kind_allowed(kind: str, chat_types: str) -> bool:
    if chat_types == "all":
        return True
    if chat_types == "private":
        return kind == "private"
    if chat_types == "groups":
        return kind == "group"
    if chat_types == "channels":
        return kind == "channel"
    return True


def _message_limit_for_kind(registry: ServiceRegistry, kind: str) -> int | None:
    """Per-dialog search depth. ``None`` means scan to the oldest message."""
    settings = registry.settings
    if kind == "group":
        return max(1, settings.jarvis_archive_group_message_limit)
    if kind == "channel":
        return max(1, settings.jarvis_archive_channel_message_limit)
    if kind == "private":
        limit = settings.jarvis_archive_private_message_limit
        return None if limit <= 0 else max(1, limit)
    return max(1, settings.jarvis_archive_messages_per_dialog)


def _message_limits(registry: ServiceRegistry) -> dict[str, int | str]:
    private = registry.settings.jarvis_archive_private_message_limit
    return {
        "group": max(1, registry.settings.jarvis_archive_group_message_limit),
        "channel": max(1, registry.settings.jarvis_archive_channel_message_limit),
        "private": "all" if private <= 0 else max(1, private),
        "fallback": max(1, registry.settings.jarvis_archive_messages_per_dialog),
    }


async def _search_index(
    registry: ServiceRegistry,
    *,
    query: str,
    chat_name: str | None,
    chat_types: str,
    media_type: str,
    scope: str,
    limit: int,
    target: Path,
) -> list[ArchiveSearchResult]:
    tokens = _tokens(" ".join(x for x in (query, chat_name or "") if x))
    if not tokens and not chat_name:
        return []
    since = _scope_since(scope, tz=registry.settings.user_timezone)
    async with registry.session() as session:
        rows = await telegram_archive_repo.search_messages(
            session,
            tokens=tokens,
            chat_kinds=_chat_kinds_for_request(chat_types),
            media_kinds=_media_kinds_for_request(media_type),
            since=since,
            candidate_limit=max(80, min(500, limit * 80)),
        )

    ranked: list[tuple[int, Any]] = []
    for row in rows:
        if chat_name and not _name_matches(chat_name, row.chat_title):
            continue
        searchable = " ".join(
            x
            for x in (
                row.text or "",
                row.analysis_text or "",
                row.chat_title or "",
                row.sender_label or "",
            )
            if x
        )
        strong = _strong_match(query, searchable)
        score = _score(query, searchable)
        if row.analysis_text:
            score += _score(query, row.analysis_text)
        if chat_name and _name_matches(chat_name, row.chat_title):
            score += 2
        if strong:
            score += 3
        if score < 2 and not strong:
            continue
        ranked.append((score, row))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[1].sent_at or datetime.min.replace(tzinfo=ZoneInfo("UTC")),
        ),
        reverse=True,
    )

    results: list[ArchiveSearchResult] = []
    for _, row in ranked[: max(1, min(limit, 10))]:
        path = None
        if row.media_kind != "text":
            path = await _download_indexed_media(registry, row, target)
        results.append(
            ArchiveSearchResult(
                chat_title=row.chat_title,
                chat_kind=row.chat_kind,
                sender=row.sender_label,
                sent_at=row.sent_at,
                text=row.text or "",
                media_kind=row.media_kind,
                path=path,
                description=row.analysis_text,
                dialog_id=row.dialog_id,
                message_id=row.message_id,
            )
        )
    return results


def _chat_kinds_for_request(chat_types: str) -> set[str] | None:
    if chat_types == "private":
        return {"private"}
    if chat_types == "groups":
        return {"group"}
    if chat_types == "channels":
        return {"channel"}
    return None


def _media_kinds_for_request(media_type: str) -> set[str] | None:
    if media_type == "any":
        return None
    if media_type == "text":
        return {"text"}
    if media_type == "audio":
        return {"audio", "voice"}
    return {media_type}


async def _download_indexed_media(
    registry: ServiceRegistry, row: Any, target: Path
) -> str | None:
    client = registry.userbot
    if client is None:
        return None
    try:
        entity = await _get_entity_for_dialog_id(registry, int(row.dialog_id))
        if entity is None:
            return None
        message = await client.get_messages(entity, ids=int(row.message_id))
        if message is None:
            return None
        return await _download_media(client, message, target)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "telegram_archive.index_media_download_failed",
            dialog_id=row.dialog_id,
            message_id=row.message_id,
            error=str(exc)[:160],
        )
        return None


async def _get_entity_for_dialog_id(registry: ServiceRegistry, dialog_id: int) -> Any | None:
    client = registry.userbot
    if client is None:
        return None
    try:
        return await client.get_entity(dialog_id)
    except Exception:
        pass
    async for dialog in client.iter_dialogs(
        limit=max(1, registry.settings.jarvis_archive_dialog_limit)
    ):
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        value = getattr(dialog, "id", None)
        if value is None:
            value = getattr(entity, "id", None)
        if value is not None and int(value) == int(dialog_id):
            return entity
    return None


def _name_matches(needle: str, title: str) -> bool:
    n = normalize_name(needle)
    t = normalize_name(title)
    if not n or not t:
        return False
    if n in t or t in n:
        return True
    needle_tokens = _chat_tokens(needle)
    title_tokens = _chat_tokens(title)
    if not needle_tokens or not title_tokens:
        return False
    overlap = set(needle_tokens) & set(title_tokens)
    if any(token.isdigit() and len(token) >= 4 for token in overlap):
        return True
    return any(
        SequenceMatcher(None, a, b).ratio() >= 0.78
        for a in needle_tokens
        for b in title_tokens
        if len(a) >= 5 and len(b) >= 5
    )


def _chat_tokens(text: str) -> list[str]:
    tokens = [normalize_name(t) for t in _WORD_RE.findall(text or "")]
    return [t for t in tokens if t and t not in _CHAT_STOPWORDS]


def _log_search_done(
    *,
    query: str,
    chat_name: str | None,
    chat_types: str,
    media_type: str,
    scanned_dialogs: int,
    matched_dialogs: int,
    scanned_messages: int,
    media_seen: int,
    analyzed: int,
    results: int,
    message_limits: dict[str, int | str],
) -> None:
    logger.info(
        "telegram_archive.search.done",
        query=query[:120],
        chat_name=chat_name,
        chat_types=chat_types,
        media_type=media_type,
        scanned_dialogs=scanned_dialogs,
        matched_dialogs=matched_dialogs,
        scanned_messages=scanned_messages,
        media_seen=media_seen,
        analyzed=analyzed,
        results=results,
        message_limits=message_limits,
    )


def _message_media_kind(message: Any) -> str:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "video_note", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "document", None):
        mime = _message_mime_type(message)
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
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


def _media_allowed(message_kind: str, requested: str) -> bool:
    if requested == "any":
        return True
    if requested == "text":
        return message_kind == "text"
    if requested == "audio":
        return message_kind in {"audio", "voice"}
    return message_kind == requested


def _query_wants_media_analysis(query: str, requested: str, message_kind: str) -> bool:
    if requested in {"voice", "audio"}:
        return message_kind in {"voice", "audio"}
    if requested in {"video", "photo"}:
        return message_kind == requested
    if message_kind in {"voice", "audio"}:
        return _VOICE_HINT_RE.search(query) is not None
    if message_kind in {"video", "photo"}:
        return _VISUAL_HINT_RE.search(query) is not None
    return False


def _tokens(text: str) -> set[str]:
    return {normalize_name(t) for t in _WORD_RE.findall(text or "") if len(t) >= 3}


def _score(query: str, text: str) -> int:
    if not text:
        return 0
    q = _tokens(query)
    hay = _tokens(text)
    return len(q & hay)


def _strong_match(query: str, text: str) -> bool:
    q = normalize_name(query)
    h = normalize_name(text)
    if not q or not h:
        return False
    return q in h or h in q


async def _download_media(client: Any, message: Any, target: Path) -> str | None:
    try:
        downloaded = await client.download_media(message, file=str(target))
        return str(downloaded) if downloaded else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_archive.download_failed", error=str(exc))
        return None


async def _sender_label(message: Any) -> str:
    if getattr(message, "out", False):
        return "Siz"
    try:
        sender = await message.get_sender()
    except Exception:  # noqa: BLE001
        sender = getattr(message, "sender", None)
    if sender is None:
        return "Noma'lum"
    first = (getattr(sender, "first_name", None) or "").strip()
    last = (getattr(sender, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    title = (getattr(sender, "title", None) or "").strip()
    username = (getattr(sender, "username", None) or "").strip()
    if full and username:
        return f"{full} (@{username})"
    return full or title or (f"@{username}" if username else "Noma'lum")


async def _describe_or_transcribe(
    registry: ServiceRegistry,
    message: Any,
    path: str,
    *,
    query: str,
    media_kind: str,
) -> str:
    key = _cache_key(message, media_kind)
    cached = await _cache_get(registry, key)
    if cached:
        return cached
    if media_kind in {"voice", "audio"}:
        text = await _transcribe_media(registry, path)
    else:
        text = await _describe_visual(registry, path, query=query)
    if text:
        await _cache_set(registry, key, text)
        dialog_id = _message_dialog_id(message)
        message_id = getattr(message, "id", None)
        if dialog_id is not None and message_id is not None:
            async with registry.session() as session:
                await telegram_archive_repo.update_message_analysis(
                    session,
                    dialog_id=dialog_id,
                    message_id=int(message_id),
                    analysis_text=text,
                )
    return text


def _cache_key(message: Any, media_kind: str) -> str:
    chat_id = _message_dialog_id(message) or getattr(message, "peer_id", "x")
    message_id = getattr(message, "id", "x")
    return f"telegram_archive:{media_kind}:{chat_id}:{message_id}"


def _message_dialog_id(message: Any) -> int | None:
    value = getattr(message, "chat_id", None)
    if value is None:
        peer = getattr(message, "peer_id", None)
        value = (
            getattr(peer, "channel_id", None)
            or getattr(peer, "chat_id", None)
            or getattr(peer, "user_id", None)
        )
    return int(value) if value is not None else None


async def _cache_get(registry: ServiceRegistry, key: str) -> str | None:
    async with registry.session() as session:
        value = await setting_repo.get_value(session, key)
    if isinstance(value, str):
        return value
    return None


async def _cache_set(registry: ServiceRegistry, key: str, value: str) -> None:
    async with registry.session() as session:
        await setting_repo.set_value(session, key, value[:4000])


async def _transcribe_media(registry: ServiceRegistry, path: str) -> str:
    voice = registry.voice_service
    if voice is None or not voice.can_transcribe():
        return ""
    mime = mimetypes.guess_type(path)[0] or "audio/ogg"
    try:
        return await voice.transcribe(path, mime_type=mime, hint_names=[])
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_archive.voice_transcribe_failed", error=str(exc))
        return ""


async def _describe_visual(registry: ServiceRegistry, path: str, *, query: str) -> str:
    settings = registry.settings
    max_bytes = max(1, settings.jarvis_archive_media_max_mb) * 1024 * 1024
    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except OSError:
        return ""
    if size > max_bytes:
        logger.info("telegram_archive.visual_skipped_large", size=size, path=path)
        return ""
    gemini = get_gemini_client(settings)
    if gemini is None:
        return ""
    try:
        from google.genai import types

        data = await asyncio.to_thread(Path(path).read_bytes)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        prompt = (
            "Telegramdagi ushbu media ichida nima tasvirlanganini o'zbek lotinida "
            "qisqa tasvirla. Foydalanuvchi qidirayotgan ma'no: "
            f"{query!r}. Agar mos bo'lsa buni ham aniq ayt. Faqat 1-2 jumla."
        )
        response = await gemini.aio.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
            config=types.GenerateContentConfig(temperature=0),
        )
        return (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_archive.visual_describe_failed", error=str(exc)[:160])
        return ""
