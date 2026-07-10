"""Gemini intent router — the Google Gemini equivalent of :class:`IntentRouter`.

Performs one ``generate_content`` call with native **structured output**
(``response_mime_type="application/json"`` + ``response_schema=NLUResult``), so
Gemini constrains its decoding to the envelope in :mod:`app.brain.nlu_schema`.
The chosen ``intent`` plus its filled sub-object are mapped to a
:class:`~app.brain.intent_router.RoutedIntent` the dispatcher can act on — so
the rest of the pipeline stays provider-agnostic.

When no Gemini client is configured the router is built with a ``None`` client;
:meth:`route` then raises a clear, Uzbek-friendly :class:`RuntimeError`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.brain.intent_router import INTENT_MODELS, RoutedIntent
from app.brain.nlu_schema import NLUMultiResult, NLUResult
from app.brain.prompts import ROUTER_SYSTEM_MULTI, ROUTER_SYSTEM_STRUCTURED
from app.config import get_settings
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger

logger = get_logger(__name__)

# Transient server-side failures (503 high-demand / 500 internal) are retried.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
# Hard cap per attempt so a stalled call can never hang the bot ("Bajarilmoqda"
# forever). A timeout is treated like a transient error and retried — kept tight
# so an intermittent Vertex stall recovers on the next attempt quickly.
_CALL_TIMEOUT = 20.0


def _schema_hint(model: type) -> str:
    """Describe ``model``'s JSON shape in the prompt for the API-key path.

    The AI-Studio (Gemini API-key) endpoint rejects our large intent-envelope as
    a ``response_schema`` ("schema produces a constraint that has too many states
    for serving"), while Vertex accepts it. So on the key path we DROP the schema
    and instead spell out the exact JSON shape in the system prompt — schema TEXT
    has no constrained-decoding limit — then validate the reply locally. Field
    names/enums come straight from the same pydantic models, so nothing drifts.
    """
    return (
        "\n\nReturn a JSON object matching EXACTLY this JSON Schema. Fill only the "
        "sub-object whose key equals `intent` (leave every other sub-object null):\n"
        + json.dumps(model.model_json_schema())
    )


class GeminiIntentRouter:
    """Wraps the Gemini client and routes utterances to intents."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.client = client if client is not None else get_gemini_client(settings)
        self.model = model or settings.gemini_nlu_model
        # Vertex honours native ``response_schema``; the free API-key path can't
        # (schema too large) and is guided by ``_schema_hint`` in the prompt.
        self.use_response_schema = bool(settings.gemini_use_vertex)

    async def _generate_with_retry(self, *, contents: str, config: Any) -> Any:
        """Call Gemini, retrying transient 5xx (high-demand/internal) failures."""
        from google.genai import errors as genai_errors

        delay = _RETRY_BASE_DELAY
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=self.model, contents=contents, config=config
                    ),
                    timeout=_CALL_TIMEOUT,
                )
            except (genai_errors.ServerError, TimeoutError) as exc:
                if attempt == _RETRY_ATTEMPTS - 1:
                    raise
                logger.warning(
                    "gemini_router.retry",
                    attempt=attempt + 1,
                    error=str(exc)[:120] or type(exc).__name__,
                )
                await asyncio.sleep(delay)
                delay *= 2

    @staticmethod
    def _parse(response: Any) -> NLUResult | None:
        """Extract an :class:`NLUResult` from a Gemini response (``None`` on fail).

        Prefers the SDK's auto-parsed ``response.parsed`` (an ``NLUResult`` when
        ``response_schema`` is honoured); falls back to validating the raw JSON
        text so a fake/older SDK that only fills ``.text`` still works.
        """
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, NLUResult):
            return parsed
        text = getattr(response, "text", None)
        if text:
            try:
                return NLUResult.model_validate_json(text)
            except Exception:  # noqa: BLE001 — surface as "unknown", never crash
                logger.exception("gemini_router.parse_failed", text=str(text)[:200])
        return None

    async def route(self, utterance: str, *, now_iso: str) -> RoutedIntent:
        """Route ``utterance`` to a validated :class:`RoutedIntent` via Gemini."""
        if self.client is None:
            raise RuntimeError(
                "Sun'iy intellekt sozlanmagan: GEMINI_API_KEY topilmadi. "
                "aistudio.google.com dan tekin kalit oling va .env ga qo'shing."
            )

        from google.genai import types

        if self.use_response_schema:
            config = types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_STRUCTURED,
                response_mime_type="application/json",
                response_schema=NLUResult,
                temperature=0,
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_STRUCTURED + _schema_hint(NLUResult),
                response_mime_type="application/json",
                temperature=0,
            )

        response = await self._generate_with_retry(
            contents=f"<now>{now_iso}</now> {utterance}", config=config
        )

        result = self._parse(response)
        if result is None:
            logger.info("gemini_router.no_result")
            return RoutedIntent("unknown", None, {})

        routed = self._to_routed(result)
        if routed.name == "unknown":
            logger.info("gemini_router.no_intent", intent=result.intent)
        elif routed.name == "search_telegram_archive":
            logger.info("gemini_router.routed", tool=routed.name, params=routed.raw_input)
        else:
            logger.info("gemini_router.routed", tool=routed.name)
        return routed

    @staticmethod
    def _to_routed(result: NLUResult) -> RoutedIntent:
        """Map one validated :class:`NLUResult` envelope to a :class:`RoutedIntent`."""
        name = result.intent
        params = getattr(result, name, None) if name != "unknown" else None
        # Defensive: the model named an intent but left its sub-object empty —
        # adopt whichever sub-object it actually filled, if any.
        if params is None and name != "unknown":
            for candidate in INTENT_MODELS:
                filled = getattr(result, candidate, None)
                if filled is not None:
                    name, params = candidate, filled
                    break
        if params is None:
            return RoutedIntent("unknown", None, {})
        return RoutedIntent(name, params, params.model_dump())

    @staticmethod
    def _parse_multi(response: Any) -> NLUMultiResult | None:
        """Extract an :class:`NLUMultiResult` from a Gemini response (``None`` on fail)."""
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, NLUMultiResult):
            return parsed
        text = getattr(response, "text", None)
        if text:
            try:
                return NLUMultiResult.model_validate_json(text)
            except Exception:  # noqa: BLE001 — surface as single-route fallback
                logger.exception("gemini_router.parse_multi_failed", text=str(text)[:200])
        return None

    async def route_many(self, utterance: str, *, now_iso: str) -> list[RoutedIntent]:
        """Split one utterance into one or more ordered :class:`RoutedIntent`s.

        Uses a multi-action structured schema so a single message carrying
        several commands ("... ayt, hozir ogohlantir va ... ogohlantir") routes
        to all of them. Falls back to single :meth:`route` when the model returns
        no usable actions.
        """
        if self.client is None:
            raise RuntimeError(
                "Sun'iy intellekt sozlanmagan: GEMINI_API_KEY topilmadi. "
                "aistudio.google.com dan tekin kalit oling va .env ga qo'shing."
            )

        from google.genai import types

        if self.use_response_schema:
            config = types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_MULTI,
                response_mime_type="application/json",
                response_schema=NLUMultiResult,
                temperature=0,
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_MULTI + _schema_hint(NLUMultiResult),
                response_mime_type="application/json",
                temperature=0,
            )
        response = await self._generate_with_retry(
            contents=f"<now>{now_iso}</now> {utterance}", config=config
        )
        multi = self._parse_multi(response)
        if multi is None or not multi.actions:
            return [await self.route(utterance, now_iso=now_iso)]
        # Cap defensively so a runaway split can never spawn a huge action chain.
        routed = [self._to_routed(a) for a in multi.actions[:8]]
        logger.info("gemini_router.routed_many", tools=[r.name for r in routed])
        return routed
