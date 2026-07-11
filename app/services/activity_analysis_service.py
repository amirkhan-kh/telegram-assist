"""Analytical Q&A over the owner's personal-productivity data.

Answers open/analytical questions that span reminders, tasks, meetings, debts,
important dates and decisions — "shu oyda nechta uchrashuvim bor", "eng katta
qarzim kimda", "bu hafta nima ko'p", "bajarilmagan vazifalarim qaysi" — which the
plain per-domain list intents can't reason about.

It reuses the existing, tested ``list_*`` handlers to gather an already-formatted
snapshot of each domain, then lets the LLM answer the specific question over that
snapshot in natural Uzbek — and, like a human, explains clearly when something
can't be determined instead of dead-ending.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import TYPE_CHECKING

from app.brain import intents as I
from app.brain.intent_router import RoutedIntent
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

_TIMEOUT = 40.0
_TAG_RE = re.compile(r"<[^>]+>")

# (label, intent) pairs — each reuses a read-only list handler for its snapshot.
_SOURCES = [
    ("Bugungi va umumiy reja", "list_agenda", lambda: I.ListAgenda(scope="all")),
    ("Eslatmalar", "list_reminders", lambda: I.ListReminders()),
    ("Uchrashuvlar", "list_meetings", lambda: I.ListMeetings(scope="all")),
    ("Moliya / qarzlar", "list_finance", lambda: I.ListFinance(direction="all")),
    ("Muhim sanalar", "list_important_dates", lambda: I.ListImportantDates()),
    ("Qarorlar", "list_decisions", lambda: I.ListDecisions()),
]

_SYSTEM = (
    "Sen — Telegram shaxsiy yordamchining reja/vazifa tahlil moduli. Senga egasining "
    "eslatmalari, vazifalari, uchrashuvlari, qarzlari, muhim sanalari va qarorlari "
    "bo'yicha joriy holat beriladi. Egasining savoliga o'zbek tilida (lotin), aniq va "
    "foydali javob ber.\n"
    "- Faqat berilgan ma'lumotga tayan; o'zingdan fakt to'qima.\n"
    "- Sanash/taqqoslash so'ralsa — ma'lumotdagi qatorlarni sanab, aniq son ber.\n"
    "- Agar biror narsa ma'lumotda yo'q bo'lsa — buni ochiq, inson kabi tushuntir: "
    "aynan nega javob berolmayotganingni va egasi qanday qo'shsa/so'rasa yordam bera "
    "olishingni ayt. Shunchaki 'yo'q' deb qo'yma.\n"
    "- Markdown belgilari (*, **, #) ISHLATMA — oddiy matn; ro'yxatда '• ' ishlat.\n"
    "- Qisqa, aniq, samimiy."
)


def _history_block(history: list[tuple[str, str]] | None) -> str:
    """Recent conversation turns as context so follow-ups continue naturally."""
    if not history:
        return ""
    lines = [
        f"{'Egasi' if role == 'user' else 'Sen'}: {text}"
        for role, text in history[-8:]
    ]
    return "AVVALGI SUHBAT (kontekst — ergashuvchi savollarga shu asosda javob ber):\n" + "\n".join(lines) + "\n\n"


async def analyze_activity(
    registry: ServiceRegistry,
    *,
    query: str,
    now: datetime,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Answer ``query`` over the owner's activity snapshot (never raises)."""
    from app.services.dispatcher import dispatch

    blocks: list[str] = []
    for label, name, make in _SOURCES:
        try:
            result = await dispatch(registry, RoutedIntent(name, make(), {}), now=now)
            text = _TAG_RE.sub("", result.text or "").strip()
            if text:
                blocks.append(f"### {label}:\n{text}")
        except Exception as exc:  # noqa: BLE001 — one domain failing must not abort
            logger.warning("activity.snapshot.failed", domain=name, error=str(exc)[:120])

    if not blocks:
        return (
            "Hozircha tahlil qiladigan reja/vazifa ma'lumoti yo'q. Eslatma, uchrashuv, "
            "qarz yoki qaror qo'shsangiz — ular bo'yicha har qanday savolga javob beraman."
        )

    snapshot = "\n\n".join(blocks)
    client = get_gemini_client(registry.settings)
    if client is None:
        return (
            "AI hozir sozlanmagan, shuning uchun chuqur tahlil qilolmadim. Mana joriy "
            f"holatingiz:\n\n{snapshot}"
        )

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=registry.settings.gemini_nlu_model,
                contents=(
                    _history_block(history)
                    + f"JORIY HOLAT:\n{snapshot}\n\n"
                    + f"EGASINING SAVOLI: {query or 'Ishlarimni umumiy tahlil qilib ber.'}"
                ),
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM, temperature=0.2
                ),
            ),
            timeout=_TIMEOUT,
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — degrade to the raw snapshot, never crash
        logger.warning("activity.analyze.failed", error=str(exc)[:160])
    return f"Batafsil tahlil hozir bo'lmadi, lekin joriy holatingiz:\n\n{snapshot}"
