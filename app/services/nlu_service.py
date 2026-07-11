"""NluService — thin wrapper around :class:`app.brain.intent_router.IntentRouter`.

Caches a single :class:`IntentRouter` and exposes ``route`` plus ``available``
so the bot text/voice handlers can check up front whether the NLU brain is
configured (an Anthropic key is present) and reuse one client.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.brain.contacts import clean_contact_query
from app.brain.intent_router import RoutedIntent
from app.brain.intents import (
    DeliveryMode,
    GetChatMessages,
    ScheduleMeeting,
    ScheduleMessage,
    SearchChatMedia,
    SendMessage,
    TimeSpec,
)
from app.brain.router_factory import build_router
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_QUOTE_RE = re.compile(r"[\"“”«»](.*?)[\"“”«»]")
_SEND_WORD_RE = re.compile(
    r"\b(?:xabar|yubor|jo['‘’ʻʼ`]?nat|yo['‘’ʻʼ`]?lla|yolla|yoz|ayt)\b",
    re.IGNORECASE,
)
_META_LOG_CHECK_RE = re.compile(
    r"^\s*(?:log(?:ni|larni)?|log\s+ni|log\s+larni)\s+tekshir\b",
    re.IGNORECASE,
)
_DELIVERY_VOICE_RE = re.compile(r"\b(?:ovozli|ovozda|audio)\b", re.IGNORECASE)
_DELIVERY_TEXT_RE = re.compile(r"\b(?:matn|tekst)\b", re.IGNORECASE)
_TRAILING_SEND_RE = re.compile(
    r"\s*(?:deb\s+)?(?:xabar\s+)?(?:yubor(?:ing)?|jo['‘’ʻʼ`]?nat(?:ing)?|"
    r"yo['‘’ʻʼ`]?lla(?:ng)?|yolla(?:ng)?|yoz(?:ing)?|ayt(?:ing)?)\.?\s*$",
    re.IGNORECASE,
)
_LEADING_RECIPIENT_SUFFIX_RE = re.compile(
    r"^\s*(?:raq[ai]?m(?:i)?ga|nomer(?:i)?ga|telefon(?:i)?ga|ga|ka|qa)\b[,:\s-]*",
    re.IGNORECASE,
)
_LEADING_DELIVERY_RE = re.compile(
    r"^\s*(?:ovozli|ovozda|audio|matn|tekst)\s+", re.IGNORECASE
)
_COMMAND_PREFIX_RE = re.compile(
    r"^\s*(?:(?:iltimos|joni|jarvis|joniy)\b[,:\s]*)+", re.IGNORECASE
)
_MEDIA_PHOTO_RE = re.compile(r"\b(?:rasm|foto|surat)\w*\b", re.IGNORECASE)
_MEDIA_VIDEO_RE = re.compile(
    r"\b(?:video|vidyo|dumaloq\s+video|mp4)\w*\b", re.IGNORECASE
)
_MEDIA_DOCUMENT_RE = re.compile(r"\b(?:fayl|hujjat|document)\w*\b", re.IGNORECASE)
_MEDIA_ANY_RE = re.compile(r"\b(?:media|rasm\s+va\s+video|video\s+va\s+rasm)\b", re.IGNORECASE)
_TEXT_MESSAGE_RE = re.compile(r"\b(?:xabar|yozishma|sms|matn)\w*\b", re.IGNORECASE)
_INCOMING_READ_RE = re.compile(
    r"\b(?:menga|meni|u\s+menga)\s+"
    r"(?:yuborgan|jo['‘’ʻʼ`]?natgan|tashlagan|yozgan|aytgan)\b",
    re.IGNORECASE,
)
_OUTGOING_READ_RE = re.compile(
    r"\b(?:men\s+(?:unga\s+)?|o['‘’ʻʼ`]?zim\s+)?"
    r"(?:yuborgan|jo['‘’ʻʼ`]?natgan|tashlagan|yozgan)\s+"
    r"(?:xabarim|xabarlarim|rasmim|rasmlarim|videom|videolarim|faylim|fayllarim)\b",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(r"\boxirgi\s+(\d{1,2})\s*ta\b", re.IGNORECASE)
_CONTACT_SEND_SKIP_RE = re.compile(
    r"\b(?:menga|meni|u\s+menga)\s+"
    r"(?:yuborgan|jo['‘’ʻʼ`]?natgan|tashlagan|yozgan|aytgan)\b",
    re.IGNORECASE,
)
_FUTURE_HINT_RE = re.compile(
    r"\b(?:ertaga|indin|keyin|soat\s+\d|dushanba|seshanba|chorshanba|"
    r"payshanba|juma|shanba|yakshanba|hafta|oy|sana)\b",
    re.IGNORECASE,
)
_MEETING_CREATE_RE = re.compile(
    r"(?P<name>.+?)\s+bilan\s+(?P<when>.+?)\s+"
    r"(?P<title>miting|meeting|uchrashuv)\w*\s+"
    r"(?:belgila|belgilab\s+qo['‘’ʻʼ`]?y|rejalashtir|qo['‘’ʻʼ`]?y)",
    re.IGNORECASE,
)
_NOTIFY_RE = re.compile(
    r"\b(?:ogohlantir|xabardor\s+qil|xabar\s+ber|ayt|eslat)\b",
    re.IGNORECASE,
)
_NOW_RE = re.compile(r"\b(?:hozir|endi|darhol)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(
    r"(?:(?P<day>bugun|ertaga|indin)\s+)?(?:soat\s*)?"
    r"(?P<hour>\d{1,2})(?P<sep>[:.])(?P<minute>\d{2})",
    re.IGNORECASE,
)
_HOUR_RE = re.compile(
    r"(?:(?P<day>bugun|ertaga|indin)\s+)?soat\s+(?P<hour>\d{1,2})(?!\s*[:.]\d)",
    re.IGNORECASE,
)


def _compact_phone(raw: str) -> str:
    """Keep the spoken phone number exact, but remove separators."""
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if (raw or "").strip().startswith("+") else digits


def _direct_phone_send(utterance: str) -> RoutedIntent | None:
    """Route explicit '<phone> raqamiga ... xabar yubor' commands locally.

    LLMs sometimes normalize or rewrite phone numbers. A raw phone recipient is
    already structured enough, so preserve it before the generic router sees it.
    """
    text = _strip_command_prefix(utterance)
    if _META_LOG_CHECK_RE.search(text):
        return None
    if not text or _SEND_WORD_RE.search(text) is None:
        return None
    phone_match = _PHONE_RE.search(text)
    if phone_match is None:
        return None

    recipient = _compact_phone(phone_match.group(0))
    if len(re.sub(r"\D", "", recipient)) < 7:
        return None

    quoted = _QUOTE_RE.search(text)
    if quoted is not None and quoted.group(1).strip():
        content = quoted.group(1).strip()
    else:
        rest = text[phone_match.end():]
        rest = _LEADING_RECIPIENT_SUFFIX_RE.sub("", rest)
        content = _TRAILING_SEND_RE.sub("", rest).strip(" ,:;.-")
        content = _LEADING_DELIVERY_RE.sub("", content).strip(" ,:;.-")
    if not content:
        return None

    delivery = DeliveryMode.ask
    if _DELIVERY_VOICE_RE.search(text):
        delivery = DeliveryMode.voice
    elif _DELIVERY_TEXT_RE.search(text):
        delivery = DeliveryMode.text

    params = SendMessage(
        recipient_name=recipient,
        content=content,
        delivery=delivery,
    )
    return RoutedIntent("send_message", params, params.model_dump())


def _direct_contact_read(utterance: str) -> RoutedIntent | None:
    """Route common private-chat read/media commands locally.

    This catches high-frequency Uzbek voice commands such as
    "Asadbek, menga yuborgan oxirgi xabarni yubor" before a generic LLM can
    mistake the phrase as an outbound message body.
    """
    text = _strip_command_prefix(utterance)
    if not text:
        return None
    incoming = _INCOMING_READ_RE.search(text)
    outgoing = _OUTGOING_READ_RE.search(text)
    if incoming is None and outgoing is None:
        return None

    marker = incoming or outgoing
    if marker is None:
        return None
    contact = _clean_direct_contact(text[: marker.start()])
    if not contact:
        return None
    direction = "incoming" if incoming is not None else "outgoing"
    limit = _extract_limit(text, default=1)
    media_type = _media_type_from_text(text)
    if media_type is not None and not _TEXT_MESSAGE_RE.search(text):
        params = SearchChatMedia(
            contact_name=contact,
            media_type=media_type,
            direction=direction,
            limit=max(1, limit if limit > 1 else 5),
        )
        return RoutedIntent("search_chat_media", params, params.model_dump())

    params = GetChatMessages(
        contact_name=contact,
        direction=direction,
        scope="recent",
        limit=limit,
    )
    return RoutedIntent("get_chat_messages", params, params.model_dump())


def _direct_contact_send(utterance: str) -> RoutedIntent | None:
    """Route simple immediate '<contact>ga <content> yubor' commands locally."""
    text = _strip_command_prefix(utterance)
    if not text or _META_LOG_CHECK_RE.search(text):
        return None
    if _CONTACT_SEND_SKIP_RE.search(text) or _SEND_WORD_RE.search(text) is None:
        return None
    # Let the full NLU handle likely scheduled messages.
    if _FUTURE_HINT_RE.search(text) and _QUOTE_RE.search(text) is None:
        return None

    split = _split_dative_recipient(text)
    if split is None:
        return None
    recipient, body = split
    if _looks_like_phone_like(recipient):
        return None
    quoted = _QUOTE_RE.search(body)
    if quoted is not None and quoted.group(1).strip():
        content = quoted.group(1).strip()
    else:
        if ":" in body and re.search(r"\b(?:xabar|sms)\s+yubor", body[: body.index(":")], re.I):
            content = body.split(":", 1)[1].strip()
        else:
            content = _TRAILING_SEND_RE.sub("", body).strip(" ,:;.-")
            content = _LEADING_DELIVERY_RE.sub("", content).strip(" ,:;.-")
    if not content or len(content) > 500:
        return None

    delivery = DeliveryMode.ask
    if _DELIVERY_VOICE_RE.search(text):
        delivery = DeliveryMode.voice
    elif _DELIVERY_TEXT_RE.search(text):
        delivery = DeliveryMode.text

    params = SendMessage(
        recipient_name=recipient,
        content=content,
        delivery=delivery,
    )
    return RoutedIntent("send_message", params, params.model_dump())


def _direct_meeting_sequence(utterance: str) -> list[RoutedIntent] | None:
    """Parse common multi-action meeting commands into ordered intents."""
    text = _strip_command_prefix(utterance)
    match = _MEETING_CREATE_RE.search(text)
    if match is None:
        return None
    tail = text[match.end() :]
    if not tail or _NOTIFY_RE.search(tail) is None:
        return None

    recipient = clean_contact_query(match.group("name").strip(" ,:;.-"))
    when_raw = match.group("when").strip(" ,:;.-")
    title = _meeting_title(match.group("title"))
    content = f"{_meeting_when_label(when_raw)} dagi {title.lower()}imizni eslatib qo'yaman."

    meeting = ScheduleMeeting(
        title=title,
        when=TimeSpec(raw=when_raw, kind="absolute"),
        duration_minutes=30,
        invitee_names=[],
        create_meet_link=True,
        notify_target_name=recipient,
    )
    routed: list[RoutedIntent] = [
        RoutedIntent("schedule_meeting", meeting, meeting.model_dump())
    ]

    if _NOW_RE.search(tail):
        now_msg = SendMessage(
            recipient_name=recipient,
            content=content,
            delivery=DeliveryMode.text,
        )
        routed.append(RoutedIntent("send_message", now_msg, now_msg.model_dump()))

    for raw_clock in _notification_times(tail, meeting_when=when_raw):
        scheduled = ScheduleMessage(
            recipient_name=recipient,
            content=content,
            when=TimeSpec(raw=raw_clock, kind="absolute"),
            delivery=DeliveryMode.text,
            meeting_notice=False,
        )
        routed.append(
            RoutedIntent("schedule_message", scheduled, scheduled.model_dump())
        )

    return routed if len(routed) > 1 else None


def _meeting_title(raw: str) -> str:
    lowered = (raw or "").casefold()
    if "uchrash" in lowered:
        return "Uchrashuv"
    return "Miting"


def _meeting_when_label(raw: str) -> str:
    text = (raw or "").strip(" ,:;.-")
    return re.sub(r"\s+(?:da|ga)$", "", text, flags=re.IGNORECASE) or "belgilangan vaqt"


def _notification_times(tail: str, *, meeting_when: str) -> list[str]:
    if _NOTIFY_RE.search(tail) is None:
        return []
    day_default = _day_prefix(meeting_when)
    seen: set[str] = set()
    out: list[str] = []
    for match in _CLOCK_RE.finditer(tail):
        day = (match.group("day") or day_default or "").strip()
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        raw = f"{f'{day} ' if day else ''}soat {hour:02d}:{minute:02d}"
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    for match in _HOUR_RE.finditer(tail):
        day = (match.group("day") or day_default or "").strip()
        hour = int(match.group("hour"))
        raw = f"{f'{day} ' if day else ''}soat {hour:02d}:00"
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _day_prefix(text: str) -> str:
    lowered = (text or "").casefold()
    for day in ("bugun", "ertaga", "indin"):
        if re.search(rf"\b{day}\b", lowered):
            return day
    return ""


def _media_type_from_text(text: str) -> str | None:
    if _MEDIA_ANY_RE.search(text):
        return "any"
    if _MEDIA_PHOTO_RE.search(text):
        return "photo"
    if _MEDIA_VIDEO_RE.search(text):
        return "video"
    if _MEDIA_DOCUMENT_RE.search(text):
        return "document"
    return None


def _extract_limit(text: str, *, default: int) -> int:
    match = _COUNT_RE.search(text or "")
    if match is None:
        return default
    return max(1, min(int(match.group(1)), 10))


def _clean_direct_contact(raw: str) -> str:
    cleaned = raw.strip(" ,:;.-")
    cleaned = re.sub(r"\b(?:bilan|chatidan|chatdagi|yozishmadagi)\b.*$", "", cleaned, flags=re.I)
    return clean_contact_query(cleaned).strip(" ,:;.-")


def _split_dative_recipient(text: str) -> tuple[str, str] | None:
    tokens = text.split()
    if len(tokens) < 2:
        return None
    for idx, token in enumerate(tokens[:-1]):
        clean = token.strip(" ,:;.-")
        low = clean.casefold()
        if low in {"ga", "ka", "qa"} and idx > 0:
            name = " ".join(tokens[:idx])
            body = " ".join(tokens[idx + 1 :])
            return clean_contact_query(name), body
        for suffix in ("ga", "ka", "qa"):
            if low.endswith(suffix) and len(clean) - len(suffix) >= 3:
                stem = clean[: len(clean) - len(suffix)]
                name = " ".join([*tokens[:idx], stem])
                body = " ".join(tokens[idx + 1 :])
                return clean_contact_query(name), body
    return None


def _looks_like_phone_like(text: str) -> bool:
    return len(re.sub(r"\D", "", text or "")) >= 7


def _strip_command_prefix(text: str) -> str:
    return _COMMAND_PREFIX_RE.sub("", (text or "").strip()).strip()


# Imperative verbs that head a command. Two or more of them joined by a connector
# ("va", "keyin", a comma…) signal a message bundling several commands that the
# LLM should split into an ordered chain of intents (3–5+ supported).
_MULTI_ACTION_VERB_RE = re.compile(
    r"\b(?:yubor\w*|jo['‘’ʻʼ`]?nat\w*|yo['‘’ʻʼ`]?lla\w*|ayt\w*|yoz\w*|eslat\w*|"
    r"ogohlantir\w*|belgila\w*|rejalashtir\w*|qil\w*|top\w*|qidir\w*|"
    r"ko['‘’ʻʼ`]?rsat\w*|chiqar\w*|qo['‘’ʻʼ`]?ng['‘’ʻʼ`]?iroq|qo['‘’ʻʼ`]?y\w*|"
    r"o['‘’ʻʼ`]?rnat\w*)\b",
    re.IGNORECASE,
)
_MULTI_CONNECTOR_RE = re.compile(
    r"(?:\bva\b|\bhamda\b|\bkeyin\b|\bso['‘’ʻʼ`]?ng\b|,|;)", re.IGNORECASE
)


def _looks_multi_action(utterance: str) -> bool:
    """True when the message plausibly bundles 2+ commands (≥2 verbs + a connector)."""
    text = _strip_command_prefix(utterance or "")
    if len(_MULTI_ACTION_VERB_RE.findall(text)) < 2:
        return False
    return _MULTI_CONNECTOR_RE.search(text) is not None


class NluService:
    """Routes owner utterances to validated intents via the configured LLM."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self._router: Any | None = None

    @property
    def router(self) -> Any:
        """Lazily build and cache the provider-specific intent router."""
        if self._router is None:
            self._router = build_router(self.registry.settings)
        return self._router

    def available(self) -> bool:
        """True when the configured LLM client could be constructed (key present)."""
        return self.router.client is not None

    async def route(self, utterance: str, *, now_iso: str) -> RoutedIntent:
        """Route ``utterance`` to a :class:`RoutedIntent`."""
        direct = (
            _direct_phone_send(utterance)
            or _direct_contact_read(utterance)
            or _direct_contact_send(utterance)
        )
        if direct is not None:
            logger.info(
                "nlu.direct_shortcut",
                intent=direct.name,
                params=direct.params.model_dump() if direct.params is not None else None,
            )
            return direct
        routed = await self.router.route(utterance, now_iso=now_iso)
        # No dead-ends: anything the router can't map to an action becomes a
        # conversational answer, so the assistant always replies helpfully (or
        # asks to clarify) like a human instead of a terse "Tushunmadim".
        if routed.name == "unknown":
            from app.brain.intents import AnswerQuestion

            params = AnswerQuestion(query=utterance)
            logger.info("nlu.unknown_to_answer", utterance=utterance[:80])
            return RoutedIntent("answer_question", params, params.model_dump())
        return routed

    async def route_many(self, utterance: str, *, now_iso: str) -> list[RoutedIntent]:
        """Route one utterance into one or more ordered actions.

        Order of attempts: the deterministic meeting multi-sequence; then — when
        the text bundles several commands — the LLM multi-intent split (3–5+
        actions); otherwise the single-intent path (direct shortcuts + route),
        which preserves verbatim phone handling for ordinary one-task commands.
        """
        direct_many = _direct_meeting_sequence(utterance)
        if direct_many:
            logger.info(
                "nlu.direct_multi_shortcut",
                intents=[item.name for item in direct_many],
            )
            return direct_many
        if _looks_multi_action(utterance):
            routed = await self.router.route_many(utterance, now_iso=now_iso)
            actionable = [r for r in routed if r.name != "unknown"]
            if len(actionable) >= 2:
                logger.info("nlu.multi_intent", intents=[r.name for r in actionable])
                return actionable
        return [await self.route(utterance, now_iso=now_iso)]
