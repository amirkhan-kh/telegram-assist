"""Analytical Q&A over the owner's whole Telegram contact list.

Handles questions that reason ACROSS all contacts — duplicate names, counts,
groupings, "all contacts named X" — which a plain name lookup (``list_contacts``)
cannot. Exact figures (totals, name frequencies) are computed in code so numbers
are always correct; the LLM then answers the owner's specific question in natural
Uzbek over that data, and — crucially — explains clearly, like a human, when
something genuinely cannot be determined from the address book.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from app.brain.translit import normalize_name
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger
from app.repositories import person_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

# Safety cap on how many contacts we hand the model in one prompt.
_MAX_LIST = 6000
_TIMEOUT = 45.0

_SYSTEM = (
    "Sen — Telegram shaxsiy yordamchining kontakt-tahlil moduli. Senga egasining "
    "BUTUN kontaktlar ro'yxati va aniq statistikasi beriladi. Egasining savoliga "
    "o'zbek tilida (lotin), aniq, to'liq va foydali javob ber.\n"
    "- Sonlar/hisob uchun HAR DOIM berilgan ANIQ STATISTIKADAN foydalan (o'zing "
    "qaytadan sanama).\n"
    "- Ism/ro'yxat so'ralsa — tartibli, o'qishga oson ro'yxat qilib ber.\n"
    "- Agar biror narsani berilgan ma'lumotdan aniqlab bo'lmasa — buni ochiq, "
    "samimiy, inson kabi tushuntir: aynan NEGA aniqlay olmayotganingni (masalan bu "
    "ma'lumot kontaktlarda saqlanmagan) va egasi qanday so'rasa yordam bera "
    "olishingni ayt. Hech qachon shunchaki 'topilmadi' deb qo'yma.\n"
    "- Markdown belgilaridan (*, **, #, _) FOYDALANMA — oddiy, tekis matn yoz; "
    "ro'yxat kerak bo'lsa har qatorni '• ' bilan boshla.\n"
    "- Qisqa, aniq va samimiy bo'l."
)


def _compact_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else ""


def _build_stats(people: list) -> tuple[str, list[str]]:
    """Compute exact contact statistics (source of truth for all counts)."""
    groups: dict[str, list] = defaultdict(list)
    for p in people:
        key = normalize_name(p.display_name or "") or "(nomsiz)"
        groups[key].append(p)
    dup = sorted(
        ((k, v) for k, v in groups.items() if len(v) > 1),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    no_username = sum(1 for p in people if not p.telegram_username)
    no_phone = sum(1 for p in people if not p.phone)

    dup_lines: list[str] = []
    for _key, members in dup[:80]:
        names = ", ".join(sorted({m.display_name or "(nomsiz)" for m in members}))
        disp = members[0].display_name or "(nomsiz)"
        dup_lines.append(f"{disp} — {len(members)} ta ({names})")

    stats = (
        f"Jami kontaktlar: {len(people)}\n"
        f"Noyob ismlar soni: {len(groups)}\n"
        f"Bir xil (takrorlanuvchi) ismlar soni: {len(dup)}\n"
        f"Username'i yo'q kontaktlar: {no_username}\n"
        f"Telefon raqami yo'q kontaktlar: {no_phone}\n"
        "Eng ko'p takrorlanuvchi ismlar (nom — nechta (kimlar)):\n"
        + ("\n".join(dup_lines) if dup_lines else "(bir xil ismli kontakt yo'q)")
    )
    return stats, dup_lines


def _history_block(history: list[tuple[str, str]] | None) -> str:
    """Recent conversation turns as context so follow-ups continue naturally."""
    if not history:
        return ""
    lines = [
        f"{'Egasi' if role == 'user' else 'Sen'}: {text}"
        for role, text in history[-8:]
    ]
    return "AVVALGI SUHBAT (kontekst — 'ular', 'u', 'yana' kabi ergashuvchi savollarga shu asosda javob ber):\n" + "\n".join(lines) + "\n\n"


async def analyze_contacts(
    registry: ServiceRegistry,
    *,
    query: str,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Answer ``query`` about the owner's whole contact list (never raises)."""
    async with registry.session() as session:
        people = await person_repo.list_all(session)
    people = [p for p in people if not p.is_owner]
    if not people:
        return (
            "Hozircha kontaktlaringiz yo'q — userbot sinxronlanganda ular avtomatik "
            "yuklanadi. Shundan so'ng kontaktlar bo'yicha har qanday savolga javob beraman."
        )

    stats, dup_lines = _build_stats(people)

    client = get_gemini_client(registry.settings)
    if client is None:
        return _fallback(len(people), len(dup_lines), dup_lines, query)

    listed = people[:_MAX_LIST]
    rows = []
    for i, p in enumerate(listed, start=1):
        parts = [p.display_name or "(nomsiz)"]
        if p.telegram_username:
            parts.append("@" + p.telegram_username)
        phone = _compact_phone(p.phone)
        if phone:
            parts.append(phone)
        rows.append(f"{i}. " + " | ".join(parts))
    trailer = "" if len(people) <= _MAX_LIST else f"\n(ro'yxat {_MAX_LIST} ta bilan cheklandi)"

    contents = (
        _history_block(history)
        + f"ANIQ STATISTIKA:\n{stats}\n\n"
        + "KONTAKTLAR (nom | @username | telefon):\n"
        + "\n".join(rows)
        + trailer
        + f"\n\nEGASINING SAVOLI: {query or 'Kontaktlarim haqida umumiy tahlil ber.'}"
    )

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=registry.settings.gemini_nlu_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM, temperature=0.2
                ),
            ),
            timeout=_TIMEOUT,
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — degrade to a code answer, never crash
        logger.warning("contacts.analyze.failed", error=str(exc)[:160])
    return _fallback(len(people), len(dup_lines), dup_lines, query)


def _fallback(total: int, dup_count: int, dup_lines: list[str], query: str) -> str:
    """Code-only answer when the LLM is unavailable — still useful and honest."""
    body = "\n".join(f"• {line}" for line in dup_lines[:15])
    parts = [f"Kontaktlaringiz: jami {total} ta."]
    if dup_lines:
        parts.append(f"Bir xil ism {dup_count} marta takrorlanadi. Eng ko'plari:\n{body}")
    else:
        parts.append("Bir xil ismli takrorlanuvchi kontakt topilmadi.")
    parts.append(
        "AI hozir batafsil javob bera olmadi (ehtimol vaqtincha band). Savolingizni "
        "biroz aniqroq yozib qayta yuboring — to'liqroq tahlil qilaman."
    )
    return "\n\n".join(parts)
