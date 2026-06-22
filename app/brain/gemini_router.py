"""Gemini intent router — the Google Gemini equivalent of :class:`IntentRouter`.

Performs one ``generate_content`` call with forced function calling
(``mode=ANY``), reusing the exact same tool set as the Anthropic router (the
JSON schemas are passed straight through via ``parameters_json_schema``), then
validates the chosen function's args against the matching pydantic model and
returns a :class:`~app.brain.intent_router.RoutedIntent` the dispatcher can act
on — so the rest of the pipeline is provider-agnostic.

When no Gemini client is configured the router is built with a ``None`` client;
:meth:`route` then raises a clear, Uzbek-friendly :class:`RuntimeError`.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from app.brain.intent_router import INTENT_MODELS, RoutedIntent
from app.brain.prompts import ROUTER_SYSTEM
from app.brain.tools import build_tools
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


def _strip_unsupported(schema: Any) -> Any:
    """Recursively drop keys the Gemini schema validator rejects.

    ``additionalProperties`` is not part of the accepted subset; removing it is
    harmless for our closed intent schemas.
    """
    if isinstance(schema, dict):
        return {
            k: _strip_unsupported(v)
            for k, v in schema.items()
            if k != "additionalProperties"
        }
    if isinstance(schema, list):
        return [_strip_unsupported(v) for v in schema]
    return schema


class GeminiIntentRouter:
    """Wraps the Gemini client and routes utterances to intents."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.client = client if client is not None else get_gemini_client(settings)
        self.model = model or settings.gemini_model
        self._tool: Any | None = None

    def _tools(self) -> Any:
        """Build (once) the Gemini ``Tool`` from the shared tool definitions."""
        if self._tool is None:
            from google.genai import types

            decls = [
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters_json_schema=_strip_unsupported(t["input_schema"]),
                )
                for t in build_tools()
            ]
            self._tool = types.Tool(function_declarations=decls)
        return self._tool

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

    async def route(self, utterance: str, *, now_iso: str) -> RoutedIntent:
        """Route ``utterance`` to a validated :class:`RoutedIntent` via Gemini."""
        if self.client is None:
            raise RuntimeError(
                "Sun'iy intellekt sozlanmagan: GEMINI_API_KEY topilmadi. "
                "aistudio.google.com dan tekin kalit oling va .env ga qo'shing."
            )

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=ROUTER_SYSTEM,
            tools=[self._tools()],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY
                )
            ),
            temperature=0,
            # We declare functions but execute nothing automatically.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        response = await self._generate_with_retry(
            contents=f"<now>{now_iso}</now> {utterance}", config=config
        )

        for call in response.function_calls or []:
            name = call.name
            raw_input: dict[str, Any] = copy.deepcopy(dict(call.args or {}))
            model_cls = INTENT_MODELS.get(name)
            if model_cls is None:
                logger.warning("gemini_router.unknown_tool", tool=name)
                return RoutedIntent("unknown", None, raw_input)
            try:
                params = model_cls.model_validate(raw_input)
            except Exception:  # noqa: BLE001 — surface as "unknown", never crash
                logger.exception(
                    "gemini_router.validation_failed", tool=name, raw=raw_input
                )
                return RoutedIntent("unknown", None, raw_input)
            logger.info("gemini_router.routed", tool=name)
            return RoutedIntent(name, params, raw_input)

        logger.info("gemini_router.no_tool_use")
        return RoutedIntent("unknown", None, {})
