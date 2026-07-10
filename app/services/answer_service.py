"""AnswerService — the general-knowledge / conversational reply for the brain.

This backs the ``answer_question`` intent: anything that is NOT a device action
(general knowledge, facts, advice, definitions, translations, calculations,
chit-chat) is answered here in natural, voice-friendly Uzbek instead of the
dispatcher's "Tushunmadim" fallback.

A single Gemini ``generate_content`` call produces the reply. When the brain
flagged the question as needing live info (``needs_fresh_info``) and
``ANSWER_WEB_GROUNDING`` is on, the call attaches Google Search grounding
(Vertex — no extra key) so news/prices/current-event answers are up to date. Any
grounding error degrades to an ungrounded reply, and a missing/failed client
degrades to a clear Uzbek message — a general answer must never crash a command.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)

# Hard cap per call so a stalled answer can never hang the command (the bot's
# overall command budget is larger; this leaves room for the loading/edit flow).
_ANSWER_TIMEOUT = 25.0

# Persona + style. Joni is the owner's personal assistant; replies are spoken
# back as often as read, so they must be short, natural Uzbek with no markdown.
_SYSTEM_INSTRUCTION = (
    "Sen 'Joni' — foydalanuvchining shaxsiy yordamchisi VA agentisan, Telegram'da "
    "ishlaysan (xuddi Siri yoki Gemini Live kabi). Foydalanuvchi o'zbek tilida "
    "(lotin) gaplashadi. Sen HAM umumiy savollarga javob berasan, HAM egasining "
    "Telegram akkaunti va saqlangan kontaktlari bilan amallar bajarasan.\n"
    "SENING IMKONIYATLARING — egasi so'rasa, bular avtomatik bajariladi, shuning "
    "uchun 'qila olmayman', 'kontaktlaringizga kira olmayman' yoki 'men faqat "
    "savollarga javob beraman' DEB HECH QACHON AYTMA (bu noto'g'ri):\n"
    "- Kontaktga matn yoki OVOZLI xabar yuborish (mas: «Azizbekka salom deb yubor»).\n"
    "- Kontakt qidirish, raqamini topish (mas: «Azizbekning raqamini top»).\n"
    "- Uchrashuv/meeting belgilash, Google Meet havolasi bilan (mas: «Azizbek bilan "
    "ertaga soat 10da meeting qil»).\n"
    "- Eslatma, va'da, vazifa, qarz hisobi, muhim sanalar, kalendar, Gmail, Notion.\n"
    "- Telegram chatlardan xabar/rasm/video topish, ob-havo, kunlik briefing.\n"
    "Agar egasi imkoniyatlaring haqida so'rasa («nima qila olasan», «kontaktlarim "
    "bilan ishlaysanmi») — HA deb tasdiqla va 1-2 qisqa misol buyruq ber. Amalni "
    "bajarish uchun egasi shunchaki buyruqni aytsa kifoya.\n"
    "Javob qoidalari:\n"
    "1. Javob FAQAT o'zbek tilida (lotin), tabiiy va samimiy ohangda bo'lsin. "
    "Foydalanuvchiga 'siz' deb murojaat qil.\n"
    "2. Qisqa va lo'nda javob ber — javob ovoz orqali ham eshitilishi mumkin, "
    "shuning uchun oddiy savolga 1-3 jumla yetarli. Murakkab savolga kerakli "
    "qadar batafsil, lekin ortiqcha cho'zmasdan.\n"
    "3. Markdown sarlavhalar, jadval yoki '#'/'*' belgilaridan foydalanma — "
    "sof gaplar yoz. Zarur bo'lsa qisqa ro'yxat ('- ') ishlatsang bo'ladi.\n"
    "4. Aniq bilmasang yoki ma'lumot yetishmasa, to'qib chiqarma — buni ochiq "
    "ayt va imkon bo'lsa qanday aniqlash mumkinligini ayt.\n"
    "5. Hisob-kitob yoki tarjima so'ralsa — natijani to'g'ridan-to'g'ri ber.\n"
    "6. Agar suhbat tarixi berilsa, uni hisobga ol — keyingi savol ('uniki-chi', "
    "'aholisi qancha', 'davom et') oldingi mavzuга tegishli bo'lishi mumkin. "
    "Foydalanuvchi mavzuni o'zgartirsa, yangi mavzuga o't."
)


def _build_contents(
    history: list[tuple[str, str]] | None, query: str, now_iso: str | None
) -> str:
    """Compose the model input: optional <now>, recent Q&A turns, then the query.

    History is rendered as a compact labelled transcript (robust across SDK
    versions) so follow-up questions resolve against the previous topic.
    """
    lines: list[str] = []
    if now_iso:
        lines.append(f"<now>{now_iso}</now>")
    turns = [(r, t) for r, t in (history or []) if t and t.strip()]
    if turns:
        lines.append(
            "Suhbat tarixi (eng yangisi oxirida; follow-up savol shu kontekstga "
            "tegishli bo'lishi mumkin):"
        )
        for role, msg in turns:
            who = "Foydalanuvchi" if role == "user" else "Joni"
            lines.append(f"{who}: {msg.strip()}")
        lines.append("")
    lines.append(f"Foydalanuvchi: {query.strip()}")
    return "\n".join(lines)


async def answer_question(
    settings: Settings,
    *,
    query: str,
    needs_fresh_info: bool = False,
    now_iso: str | None = None,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Return a natural-Uzbek answer to ``query`` (degrades, never raises).

    ``history`` is the recent conversation as ``(role, text)`` pairs (role is
    "user" or "model"), so follow-up questions continue the previous topic like
    the Gemini app.
    """
    clean = (query or "").strip()
    if not clean:
        return "Savolingizni tushunmadim — takrorlay olasizmi?"

    client = get_gemini_client(settings)
    if client is None:
        logger.info("answer.no_client")
        return (
            "Umumiy savollarga javob berish uchun AI hozir sozlanmagan "
            "(Gemini kaliti topilmadi)."
        )

    contents = _build_contents(history, clean, now_iso)
    ground = bool(needs_fresh_info and settings.answer_web_grounding)

    text = await _generate(client, settings, contents, ground=ground)
    if not text and ground:
        # A grounding/tool error: retry once WITHOUT the search tool so the owner
        # still gets the model's own answer.
        logger.info("answer.grounding_retry")
        text = await _generate(client, settings, contents, ground=False)
    if not text:
        return "Javobni hozir tayyorlab bo'lmadi. Birozdan so'ng qayta urinib ko'ring."
    return text


async def _generate(
    client: object, settings: Settings, contents: str, *, ground: bool
) -> str:
    """One Gemini call; returns the reply text or ``""`` on any failure."""
    try:
        from google.genai import types
    except ImportError:
        return ""

    tools = None
    if ground:
        try:
            tools = [types.Tool(google_search=types.GoogleSearch())]
        except Exception as exc:  # noqa: BLE001 — older SDK: no grounding tool
            logger.warning("answer.grounding_tool_unavailable", error=str(exc))
            tools = None

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.3,
        tools=tools,
    )
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(  # type: ignore[attr-defined]
                model=settings.gemini_answer_model, contents=contents, config=config
            ),
            timeout=_ANSWER_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — timeout/SDK/API error: degrade, never crash
        logger.warning(
            "answer.generate_failed", grounded=ground, error=str(exc)[:160]
        )
        return ""
    return (getattr(response, "text", None) or "").strip()
