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
from typing import Any

from app.brain.intent_router import INTENT_MODELS, RoutedIntent
from app.brain.nlu_schema import NLUResult
from app.brain.prompts import ROUTER_SYSTEM_STRUCTURED
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


class GeminiIntentRouter:
    """Wraps the Gemini client and routes utterances to intents."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.client = client if client is not None else get_gemini_client(settings)
        self.model = model or settings.gemini_nlu_model

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

        config = types.GenerateContentConfig(
            system_instruction=ROUTER_SYSTEM_STRUCTURED,
            response_mime_type="application/json",
            response_schema=NLUResult,
            temperature=0,
        )

        response = await self._generate_with_retry(
            contents=f"<now>{now_iso}</now> {utterance}", config=config
        )

        result = self._parse(response)
        if result is None:
            logger.info("gemini_router.no_result")
            return RoutedIntent("unknown", None, {})

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
            logger.info("gemini_router.no_intent", intent=result.intent)
            return RoutedIntent("unknown", None, {})

        logger.info("gemini_router.routed", tool=name)
        return RoutedIntent(name, params, params.model_dump())
