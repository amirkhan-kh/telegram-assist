"""Lazy factory for the Google Gemini client (``google-genai``).

Two auth paths are supported:

* **Free (AI Studio API key)** — set ``GEMINI_API_KEY`` from
  https://aistudio.google.com/apikey. This is the zero-cost path.
* **Vertex AI (service account)** — set ``GEMINI_USE_VERTEX=true`` plus
  ``GOOGLE_CLOUD_PROJECT`` and a service-account JSON via
  ``GOOGLE_APPLICATION_CREDENTIALS``. Vertex AI is billed.

The SDK is imported inside the function so the module imports cleanly even when
``google-genai`` is not installed or nothing is configured (returns ``None``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


def get_gemini_client(settings: Settings) -> Any | None:
    """Build a Gemini ``Client``, or ``None`` when unavailable/unconfigured."""
    try:
        from google import genai
    except ImportError:
        logger.warning("gemini_client.unavailable", reason="sdk_not_installed")
        return None

    if settings.gemini_use_vertex:
        return _vertex_client(genai, settings)
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key)

    logger.info("gemini_client.disabled", reason="no_api_key")
    return None


def _vertex_client(genai: Any, settings: Settings) -> Any | None:
    """Build a Vertex AI client from a service account (billed)."""
    project = settings.google_cloud_project
    creds = None
    if settings.google_application_credentials:
        try:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                settings.google_application_credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            # Fall back to the project baked into the service account.
            project = project or getattr(creds, "project_id", None)
        except Exception as exc:  # noqa: BLE001 - bad/missing file -> disabled
            logger.warning("gemini_client.vertex.bad_credentials", error=str(exc))
            return None

    if not project:
        logger.info("gemini_client.disabled", reason="no_gcp_project")
        return None

    try:
        return genai.Client(
            vertexai=True,
            project=project,
            location=settings.google_cloud_location,
            credentials=creds,
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning("gemini_client.vertex.failed", error=str(exc))
        return None
