"""Lazy factory for the async Anthropic client used by the NLU brain.

The ``anthropic`` SDK is imported inside the function so this module imports
cleanly even when the package is not installed or no API key is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

    from app.config import Settings

logger = get_logger(__name__)


def get_async_anthropic(settings: Settings) -> AsyncAnthropic | None:
    """Build an ``AsyncAnthropic`` client, or ``None`` when unavailable.

    Returns ``None`` when no API key is configured or the SDK is not installed,
    so callers can degrade gracefully instead of crashing at import/startup.
    """
    if not settings.anthropic_api_key:
        logger.info("anthropic_client.disabled", reason="no_api_key")
        return None
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("anthropic_client.unavailable", reason="sdk_not_installed")
        return None
    return AsyncAnthropic(api_key=settings.anthropic_api_key)
