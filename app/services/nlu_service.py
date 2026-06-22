"""NluService — thin wrapper around :class:`app.brain.intent_router.IntentRouter`.

Caches a single :class:`IntentRouter` and exposes ``route`` plus ``available``
so the bot text/voice handlers can check up front whether the NLU brain is
configured (an Anthropic key is present) and reuse one client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.brain.intent_router import RoutedIntent
from app.brain.router_factory import build_router
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


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
        return await self.router.route(utterance, now_iso=now_iso)
