"""NluService — thin wrapper around :class:`app.brain.intent_router.IntentRouter`.

Caches a single :class:`IntentRouter` and exposes ``route`` plus ``available``
so the bot text/voice handlers can check up front whether the NLU brain is
configured (an Anthropic key is present) and reuse one client.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.brain.intent_router import RoutedIntent
from app.brain.intents import DeliveryMode, SendMessage
from app.brain.router_factory import build_router
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_QUOTE_RE = re.compile(r"[\"“”«»](.*?)[\"“”«»]")
_SEND_WORD_RE = re.compile(
    r"\b(?:xabar|yubor|jo['‘’ʻʼ`]?nat|yoz|ayt)\b", re.IGNORECASE
)
_META_LOG_CHECK_RE = re.compile(
    r"^\s*(?:log(?:ni|larni)?|log\s+ni|log\s+larni)\s+tekshir\b",
    re.IGNORECASE,
)
_DELIVERY_VOICE_RE = re.compile(r"\b(?:ovozli|ovozda|audio)\b", re.IGNORECASE)
_DELIVERY_TEXT_RE = re.compile(r"\b(?:matn|tekst)\b", re.IGNORECASE)
_TRAILING_SEND_RE = re.compile(
    r"\s*(?:deb\s+)?(?:xabar\s+)?(?:yubor(?:ing)?|jo['‘’ʻʼ`]?nat(?:ing)?|"
    r"yoz(?:ing)?|ayt(?:ing)?)\.?\s*$",
    re.IGNORECASE,
)
_LEADING_RECIPIENT_SUFFIX_RE = re.compile(
    r"^\s*(?:raq[ai]?m(?:i)?ga|nomer(?:i)?ga|telefon(?:i)?ga|ga|ka|qa)\b[,:\s-]*",
    re.IGNORECASE,
)
_LEADING_DELIVERY_RE = re.compile(
    r"^\s*(?:ovozli|ovozda|audio|matn|tekst)\s+", re.IGNORECASE
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
    text = (utterance or "").strip()
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
        direct = _direct_phone_send(utterance)
        if direct is not None:
            logger.info(
                "nlu.direct_phone_send",
                recipient=direct.params.recipient_name,
                content=direct.params.content,
            )
            return direct
        return await self.router.route(utterance, now_iso=now_iso)
