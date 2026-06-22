"""Google OAuth credential assembly (phase 3).

Builds google.oauth2 ``Credentials`` from a stored refresh token. The Google
libraries are imported lazily so this module imports without them installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)

# Scopes required for Calendar reads/writes, Meet link creation, and read-only
# Gmail (surfacing important/unread mail). Adding gmail.readonly means a new
# refresh token is needed — re-run ``python -m scripts.google_auth`` once.
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_credentials(
    settings: Settings, *, refresh_token: str | None = None
) -> object | None:
    """Return refreshable Google ``Credentials`` or ``None`` when not configured.

    Requires ``google_client_id``, ``google_client_secret`` and a refresh token
    (from ``refresh_token`` if given, else ``google_oauth_refresh_token``) plus
    the ``google-auth`` library. ``refresh_token`` lets callers supply a token
    resolved from the encrypted secret store instead of the env var.
    """
    token = refresh_token or settings.google_oauth_refresh_token
    if not (settings.google_client_id and settings.google_client_secret and token):
        logger.info("google_oauth.disabled", reason="missing_credentials")
        return None
    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.warning("google_oauth.unavailable", reason="libs_not_installed")
        return None
    return Credentials(
        token=None,
        refresh_token=token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
