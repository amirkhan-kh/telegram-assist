"""Lazy factory for the ElevenLabs client used for TTS/STT.

The ``elevenlabs`` SDK is imported inside the function so this module imports
cleanly even when the package is not installed or no API key is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


def get_elevenlabs(settings: Settings) -> object | None:
    """Build an ``ElevenLabs`` client, or ``None`` when unavailable.

    Returns ``None`` when no API key is configured or the SDK is not installed.
    """
    if not settings.elevenlabs_api_key:
        logger.info("elevenlabs_client.disabled", reason="no_api_key")
        return None
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        try:
            # Older/alternative SDK layouts expose ElevenLabs at the top level.
            from elevenlabs import ElevenLabs  # type: ignore[no-redef]
        except ImportError:
            logger.warning("elevenlabs_client.unavailable", reason="sdk_not_installed")
            return None
    return ElevenLabs(api_key=settings.elevenlabs_api_key)
