"""Pick the intent router for the configured LLM provider.

Both routers expose the same surface — a ``client`` attribute (``None`` when the
provider is unconfigured) and ``async route(utterance, *, now_iso) -> RoutedIntent``
— so the NLU service and the rest of the pipeline don't care which one is used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


def build_router(settings: Settings) -> Any:
    """Return the intent router matching ``settings.llm_provider``."""
    provider = (settings.llm_provider or "anthropic").strip().lower()
    if provider == "gemini":
        from app.brain.gemini_router import GeminiIntentRouter

        logger.info(
            "router.provider", provider="gemini", model=settings.gemini_nlu_model
        )
        return GeminiIntentRouter(model=settings.gemini_nlu_model)

    from app.brain.intent_router import IntentRouter

    logger.info(
        "router.provider", provider="anthropic", model=settings.anthropic_model
    )
    return IntentRouter(model=settings.anthropic_model)
