"""NotionService — archive decisions and save notes to the owner's Notion.

Optional integration: enabled only when ``NOTION_API_KEY`` and
``NOTION_PARENT_PAGE_ID`` are set (and the integration is shared with that page).
On first use the bot auto-creates its databases ("Joni — Qarorlar",
"Joni — Eslatmalar") under the parent page and caches their ids in the
``settings`` table, so the owner only has to provide a token and a page.

Calls go through the Notion REST API via ``httpx``. The explicit "save to Notion"
command surfaces failures to the owner; the automatic decision-archive hook is
best-effort and never blocks logging a decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.db.base import utcnow
from app.logging_conf import get_logger
from app.repositories import setting_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TEXT_CAP = 1900  # Notion rich-text content cap per block is 2000 chars.

_DECISIONS = ("notion_db_decisions", "Joni — Qarorlar")
_NOTES = ("notion_db_notes", "Joni — Eslatmalar")


class NotionService:
    """Create Notion pages for decisions and free-form notes."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    def available(self) -> bool:
        """True when a Notion token and a parent page id are configured."""
        s = self.registry.settings
        return bool(s.notion_api_key and s.notion_parent_page_id)

    # ── public API ────────────────────────────────────────────────────────
    async def archive_decision(
        self, *, text: str, tag: str | None, decided_at: datetime
    ) -> bool:
        """Append a decision to the Notion 'Qarorlar' database. ``True`` on success."""
        if not self.available():
            return False
        schema = {
            "Qaror": {"title": {}},
            "Sana": {"date": {}},
            "Tag": {"rich_text": {}},
        }
        db_id = await self._ensure_db(*_DECISIONS, schema)
        props: dict[str, Any] = {
            "Qaror": _title(text),
            "Sana": {"date": {"start": decided_at.date().isoformat()}},
        }
        if tag:
            props["Tag"] = {"rich_text": [{"text": {"content": tag[:_TEXT_CAP]}}]}
        await self._create_page(db_id, props, text)
        logger.info("notion.decision.archived")
        return True

    async def save_note(self, *, text: str, title: str | None = None) -> bool:
        """Save a free-form note to the Notion 'Eslatmalar' database."""
        if not self.available():
            return False
        schema = {"Eslatma": {"title": {}}, "Sana": {"date": {}}}
        db_id = await self._ensure_db(*_NOTES, schema)
        props = {
            "Eslatma": _title(title or text),
            "Sana": {"date": {"start": utcnow().date().isoformat()}},
        }
        await self._create_page(db_id, props, text)
        logger.info("notion.note.saved")
        return True

    # ── internals ─────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.registry.settings.notion_api_key}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    async def _ensure_db(self, cache_key: str, name: str, schema: dict) -> str:
        """Return the cached database id, creating the database if needed."""
        async with self.registry.session() as session:
            cached = await setting_repo.get_value(session, cache_key)
        if isinstance(cached, str) and cached:
            return cached

        parent = self.registry.settings.notion_parent_page_id
        body = {
            "parent": {"type": "page_id", "page_id": parent},
            "title": [{"type": "text", "text": {"content": name}}],
            "properties": schema,
        }
        data = await self._post("/databases", body)
        db_id = data["id"]
        async with self.registry.session() as session:
            await setting_repo.set_value(session, cache_key, db_id)
        logger.info("notion.db.created", name=name)
        return db_id

    async def _create_page(
        self, db_id: str, properties: dict, body_text: str
    ) -> dict:
        """Create a page (row) in ``db_id`` with the full text as a paragraph block."""
        body = {
            "parent": {"database_id": db_id},
            "properties": properties,
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": body_text[:_TEXT_CAP]}}
                        ]
                    },
                }
            ],
        }
        return await self._post("/pages", body)

    async def _post(self, path: str, body: dict) -> dict:
        """POST to the Notion API and return the parsed JSON (raises on error)."""
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_API}{path}", headers=self._headers(), json=body
            )
            resp.raise_for_status()
            return resp.json()


def _title(text: str) -> dict:
    """Build a Notion title property value from plain text (capped)."""
    return {"title": [{"text": {"content": text[:_TEXT_CAP]}}]}
