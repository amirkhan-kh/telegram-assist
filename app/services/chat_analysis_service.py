"""Analytical Q&A over the owner's indexed Telegram conversations.

Answers questions that reason ACROSS chats — "kim bilan ko'p yozishaman", "eng
faol chatlarim", "oxirgi hafta kim ko'p yozdi", "eng ko'p kim menga yozgan" —
from the LOCAL archive index (``telegram_archive_messages``). Per-dialog counts
are computed exactly in SQL; the LLM then answers the specific question in natural
Uzbek and is HONEST that the index is a growing sample, not the full history — so
the owner always knows the basis of the answer (the human-explanation rule).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select

from app.db.models.telegram_archive import TelegramArchiveMessage as M
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

_TIMEOUT = 40.0
_TOP = 45

_SYSTEM = (
    "Sen — Telegram shaxsiy yordamchining suhbat-tahlil moduli. Senga egasining "
    "LOKAL indeksidagi suhbatlar statistikasi beriladi (har suhbat: nechta xabar, "
    "egasi nechta yozgan, nechta kelgan, oxirgi sana). Egasining savoliga o'zbek "
    "tilida (lotin), aniq va foydali javob ber.\n"
    "- Faqat berilgan statistikaga tayan; sonlarni o'zing to'qima.\n"
    "- 'Kim bilan ko'p yozishaman' -> shaxsiy (private) suhbatlar, egasi ko'p "
    "yozgan bo'yicha. 'Kim menga ko'p yozgan' -> kelgan xabarlar bo'yicha. 'Eng "
    "faol' -> jami xabar bo'yicha.\n"
    "- MUHIM: bu ma'lumot to'liq tarix EMAS, balki fonda o'sib boruvchi lokal "
    "indeksdan. Javob oxirida buni qisqa eslatib qo'y (masalan: 'indekslangan "
    "xabarlar asosida').\n"
    "- Agar biror narsani indeksdan aniqlab bo'lmasa — insonday tushuntir: nega "
    "va indeks to'lgach javob bera olishingni ayt. Quruq 'yo'q' dema.\n"
    "- Markdown belgilari (*, **, #) ISHLATMA; ro'yxatда '• ' ishlat. Qisqa bo'l."
)


async def analyze_chats(registry: ServiceRegistry, *, query: str, now: datetime) -> str:
    """Answer ``query`` over the owner's indexed conversations (never raises)."""
    out_sum = func.sum(case((M.out.is_(True), 1), else_=0))
    async with registry.session() as session:
        total = (await session.execute(select(func.count()).select_from(M))).scalar() or 0
        if total == 0:
            return (
                "Hozircha suhbatlaringiz hali indekslanmagan — bu fonda avtomatik "
                "yig'iladi. Biroz o'tib, 'kim bilan ko'p yozishaman' yoki 'eng faol "
                "chatlarim' kabi savollarni bersangiz, to'liq javob beraman."
            )
        top = (
            await session.execute(
                select(
                    M.chat_title,
                    M.chat_kind,
                    func.count().label("t"),
                    out_sum.label("o"),
                    func.max(M.sent_at).label("last"),
                )
                .group_by(M.dialog_id, M.chat_title, M.chat_kind)
                .order_by(func.count().desc())
                .limit(_TOP)
            )
        ).all()
        cutoff = now - timedelta(days=7)
        recent = (
            await session.execute(
                select(M.chat_title, func.count())
                .where(M.sent_at >= cutoff)
                .group_by(M.dialog_id, M.chat_title)
                .order_by(func.count().desc())
                .limit(15)
            )
        ).all()
        kinds = (
            await session.execute(
                select(M.chat_kind, func.count()).group_by(M.chat_kind)
            )
        ).all()

    top_lines = []
    for title, kind, t, o, last in top:
        sent = int(o or 0)
        got = int(t) - sent
        last_s = last.strftime("%Y-%m-%d") if last else "?"
        top_lines.append(
            f"{title} [{kind}] — jami {t} (men {sent}, kelgan {got}), oxirgi {last_s}"
        )
    recent_lines = [f"{title} — {c} ta" for title, c in recent] or ["(ma'lumot yo'q)"]
    kind_lines = [f"{k}: {c}" for k, c in kinds]

    context = (
        f"INDEKS HAJMI: jami {total} ta xabar.\n"
        f"Turlar bo'yicha: {', '.join(kind_lines)}\n\n"
        "ENG KO'P XABARLI SUHBATLAR (nom [turi] — jami (men/kelgan), oxirgi):\n"
        + "\n".join(top_lines)
        + "\n\nOXIRGI 7 KUNDA ENG FAOL:\n"
        + "\n".join(recent_lines)
    )

    client = get_gemini_client(registry.settings)
    if client is None:
        return "AI hozir sozlanmagan. Mana suhbatlaringiz statistikasi:\n\n" + context

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=registry.settings.gemini_nlu_model,
                contents=f"SUHBAT STATISTIKASI:\n{context}\n\nEGASINING SAVOLI: {query}",
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM, temperature=0.2
                ),
            ),
            timeout=_TIMEOUT,
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — degrade to the raw stats, never crash
        logger.warning("chats.analyze.failed", error=str(exc)[:160])
    return "Batafsil tahlil hozir bo'lmadi, lekin suhbat statistikangiz:\n\n" + context
