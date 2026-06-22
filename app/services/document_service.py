"""DocumentService — read a personal document's expiry date from its photo.

The owner photographs a passport / car inspection / insurance; this service sends
the image to Gemini (multimodal — the same client the voice STT already uses) and
asks for the validity-end / expiry date as JSON. Returns a ``date`` or ``None``
(unreadable / no date), so the handler can fall back to asking the owner to type
it. No third-party data leaves beyond the configured Gemini provider.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

_PROMPT = (
    "Bu rasm — shaxsiy hujjat (pasport, mashina texnik ko'rigi yoki sug'urta "
    "guvohnomasi). Hujjatdagi AMAL QILISH MUDDATI / tugash sanasini (expiry, "
    "valid until, amal qiladi) aniqla. Agar bir nechta sana bo'lsa, eng kechki "
    "(tugash) sanani tanla. FAQAT JSON qaytar: {\"expiry\": \"YYYY-MM-DD\"} — "
    "sana topilmasa {\"expiry\": null}. Boshqa hech narsa yozma."
)


class DocumentService:
    """Extract a document's expiry date from a photo via Gemini vision."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    def available(self) -> bool:
        """True when a Gemini provider (API key or Vertex) is configured."""
        s = self.registry.settings
        return bool(s.gemini_api_key or s.gemini_use_vertex)

    async def extract_expiry(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> date | None:
        """Read the expiry date from the document image (``None`` if unreadable)."""
        settings = self.registry.settings
        gemini = get_gemini_client(settings)
        if gemini is None:
            logger.info("document.extract.skipped", reason="no_gemini_client")
            return None
        try:
            from google.genai import types

            response = await gemini.aio.models.generate_content(
                model=settings.gemini_model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    _PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json"
                ),
            )
            raw = (response.text or "").strip()
            payload = json.loads(raw or "{}")
        except Exception as exc:  # noqa: BLE001 — degrade to "couldn't read it"
            logger.warning("document.extract.failed", error=str(exc)[:160])
            return None
        return _parse_iso_date(payload.get("expiry"))


def _parse_iso_date(value: object) -> date | None:
    """Parse a strict ``YYYY-MM-DD`` string into a ``date`` (``None`` otherwise)."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(p) for p in parts)
        return date(year, month, day)
    except (TypeError, ValueError):
        return None
