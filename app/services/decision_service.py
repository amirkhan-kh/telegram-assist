"""DecisionService — the owner's personal decisions journal.

A thin wrapper over :mod:`app.repositories.decision_repo` so the rest of the app
(dispatcher, callback handler) talks to a service like everything else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.decision import Decision
from app.logging_conf import get_logger
from app.repositories import decision_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class DecisionService:
    """Record and list the owner's decisions."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def add(
        self, *, owner_id: int, text: str, tag: str | None = None
    ) -> Decision:
        """Append a decision to the journal, stamped with the current time.

        When Notion is configured the decision is also mirrored to the Notion
        archive (best-effort — a Notion failure never blocks logging locally).
        """
        async with self.registry.session() as session:
            decision = await decision_repo.create(
                session,
                owner_id=owner_id,
                text=text.strip(),
                tag=(tag or None),
                decided_at=utcnow(),
            )
            did = decision.id
        logger.info("decision.logged", decision_id=did)
        async with self.registry.session() as session:
            saved = await decision_repo.get(session, did)

        notion = self.registry.notion_service
        if notion is not None and notion.available():
            try:
                await notion.archive_decision(
                    text=saved.text, tag=saved.tag, decided_at=saved.decided_at
                )
            except Exception as exc:  # noqa: BLE001 - Notion must never block logging
                logger.warning("decision.notion_archive.failed", error=str(exc)[:120])
        return saved  # type: ignore[return-value]

    async def list_recent(self, owner_id: int, *, limit: int = 20) -> list[Decision]:
        """Return the owner's most recent decisions (newest first)."""
        async with self.registry.session() as session:
            return await decision_repo.list_recent(session, owner_id, limit=limit)

    async def delete(self, decision_id: int) -> bool:
        """Delete a decision (instant undo). ``True`` if it existed."""
        async with self.registry.session() as session:
            ok = await decision_repo.delete(session, decision_id)
        if ok:
            logger.info("decision.deleted", decision_id=decision_id)
        return ok
