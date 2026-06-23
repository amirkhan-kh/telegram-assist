"""Owner-facing handlers for the control bot.

The bot is the panel the owner talks to. Every inbound owner message (text or
voice) is routed through the NLU brain to a typed intent and dispatched to the
domain services; the Uzbek confirmation comes straight back as a reply.

Flow for one utterance:
  text/voice -> (STT for voice) -> NluService.route -> RoutedIntent
            -> app.services.dispatcher.dispatch -> DispatchResult -> reply

All handlers are registered behind an owner-only chat filter (see
:mod:`app.bot.application`), so they never act on a stranger's message. The
registry is stashed in ``application.bot_data`` at build time and read back here.
User-facing strings are Uzbek (latin); logs/comments are English.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from app.brain.intent_router import RoutedIntent
from app.brain.intents import (
    GetDigest,
    ListAgenda,
    ListDecisions,
    ListFinance,
    ListImportantDates,
    ShowCalendar,
)
from app.logging_conf import get_logger
from app.repositories import person_repo
from app.services.dispatcher import (
    clear_pending_compose,
    clear_pending_outbound,
    clear_pending_time,
    complete_outbound,
    dispatch,
    dispatch_compose,
    has_pending,
    has_pending_compose,
    has_pending_time,
    resume_choice,
    resume_choice_pid,
    resume_time_day,
    resume_time_text,
    resume_with_correction,
    settle_debt,
)

if TYPE_CHECKING:
    from telegram import Message

    from app.registry import ServiceRegistry

logger = get_logger(__name__)

# ── user-facing copy (Uzbek, latin) ──────────────────────────────────────────
GREETING = (
    "🤖 Assalomu alaykum! Men <b>Joni</b> — sizning shaxsiy yordamchingizman.\n\n"
    "Menga oddiy tilda yozing yoki ovozli xabar yuboring. Masalan:\n"
    "• «Ertaga soat 10 da Nodirbekka qo'ng'iroq qilishni esla»\n"
    "• «Payshanba 15:00 da investor bilan uchrashuv qo'y»\n"
    "• «Har dushanba ertalab 8 da hisobotni esla»\n"
    "• «5-avgust — Alining tug'ilgan kuni»\n"
    "• «Bugun qaror qildim: iyuldan yangi loyiha boshlaymiz»\n\n"
    "🌅 Har kuni ertalab — kun rejasini, 🌙 kechqurun — kun yakunini yuboraman.\n"
    "Pastdagi tugmalardan foydalaning yoki /help yozing."
)

HELP_TEXT = (
    "🤖 *Joni* — shaxsiy yordamchingiz. Men quyidagilarni qila olaman:\n\n"
    "⏰ *Eslatma* — «10 daqiqadan keyin suv ichishni esla»\n"
    "🔁 *Takroriy* — «har dushanba ertalab 8 da yig'ilishni esla»\n"
    "📅 *Uchrashuv* — «payshanba 15:00 da investor bilan uchrashuv» "
    "(1 kun va 1 soat oldin eslataman)\n"
    "🤝 *Va'da* — «ertaga soat 9 da to'lovni amalga oshiraman»\n"
    "✅ *Topshiriq* — «Aliga kechgacha hujjatni tayyorlashni top, nazorat qil»\n"
    "🎂 *Muhim sana* — «5-avgust Alining tug'ilgan kuni», «pasport 12-dekabr»\n"
    "📓 *Qaror* — «bugun qaror qildim: ...»\n"
    "✉️ *Xabar* — «Karimga ovozli xabar yubor: rahmat»\n"
    "💰 *Qarz* — «Vali menga 50 ming qarzdor»\n"
    "📋 *Rejam* — «bugungi rejam» / «muhim sanalarim» / «qarorlarim»\n\n"
    "💡 Eslatma kelganda «✅ Bajarildi» yoki «⏰ Keyinga» tugmasini bosing.\n"
    "Shunchaki oddiy tilda yozing — qolganini o'zim hal qilaman."
)

# Labels that trigger special flows (submenus / actions), not direct intents.
_PD_MENU_LABEL = "🪪 Ma'lumotlarim"
_REMIND_MENU_LABEL = "⏰ Menga eslat"
_EOD_MENU_LABEL = "🌙 Kun yakuni"

# Persistent quick-action keyboard shown under the input box.
_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📋 Bugungi reja", _EOD_MENU_LABEL],
        [_REMIND_MENU_LABEL, "📅 Kalendar"],
        ["📆 Muhim sanalar", _PD_MENU_LABEL],
        ["💰 Qarzlar", "📓 Qarorlarim"],
        ["📰 Yangiliklar"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Loading "skeleton" text per quick-menu button.
_MENU_LOADING = {
    "📋 Bugungi reja": "📋 Rejangiz yuklanmoqda…",
    "📅 Kalendar": "📅 Kalendar yuklanmoqda…",
    "📆 Muhim sanalar": "📆 Muhim sanalar yuklanmoqda…",
    "💰 Qarzlar": "💰 Qarzlar hisoblanmoqda…",
    "📓 Qarorlarim": "📓 Qarorlar arxivi yuklanmoqda…",
    "📰 Yangiliklar": "📰 Yangiliklar tayyorlanmoqda…",
}


def _menu_intent(text: str) -> RoutedIntent | None:
    """Map a quick-menu button label to a ready intent (dispatched without NLU)."""
    if text == "📋 Bugungi reja":
        return RoutedIntent("list_agenda", ListAgenda(scope="today"), {})
    if text == "📅 Kalendar":
        return RoutedIntent("show_calendar", ShowCalendar(scope="week"), {})
    if text == "📆 Muhim sanalar":
        return RoutedIntent("list_important_dates", ListImportantDates(), {})
    if text == "💰 Qarzlar":
        return RoutedIntent("list_finance", ListFinance(direction="they_owe_me"), {})
    if text == "📓 Qarorlarim":
        return RoutedIntent("list_decisions", ListDecisions(), {})
    if text == "📰 Yangiliklar":
        return RoutedIntent("get_digest", GetDigest(), {})
    return None


# ── personal-data flow (passport / car inspection / insurance documents) ──────
_PD_INTRO = (
    "🪪 <b>Shaxsiy ma'lumotlar</b>\n"
    "Hujjat turini tanlang va rasmini yuboring — men undagi amal qilish "
    "muddatini o'qib, <b>7 / 3 / 1 kun</b> oldin eslatib turaman. Saqlangan "
    "rasmni xohlagan vaqtda «📸 Hujjat rasmlarim» orqali qaytarib beraman.\n\n"
    "Quyidagidan birini tanlang:"
)

# Tapping a document kind asks for its PHOTO (the bot reads the expiry date from
# the image). The «📸 Hujjat rasmlarim» button re-sends what is stored.
_PD_PROMPTS = {
    "passport": (
        "🪪 <b>Pasport</b> rasmini yuboring (kamera yoki galereyadan).\n"
        "Amal qilish muddatini o'qib, 7/3/1 kun oldin eslataman."
    ),
    "inspection": (
        "🚗 <b>Texnik ko'rik</b> hujjati rasmini yuboring.\n"
        "Amal qilish muddatini o'qib, 7/3/1 kun oldin eslataman."
    ),
    "insurance": (
        "🛡 <b>Sug'urta</b> hujjati rasmini yuboring.\n"
        "Amal qilish muddatini o'qib, 7/3/1 kun oldin eslataman."
    ),
}

# Short labels for captions/fallbacks (when no event title is available yet).
_DOC_LABELS = {
    "passport": "🪪 Pasport",
    "inspection": "🚗 Texnik ko'rik",
    "insurance": "🛡 Sug'urta",
}

# In-memory state keyed by owner chat id (short live exchanges; non-persistent):
#  - _PD_PHOTO_PENDING: awaiting the document PHOTO for this kind;
#  - _PD_PHOTO_REVIEW: an uploaded photo + read expiry, awaiting Save/Cancel;
#  - _PD_PENDING: awaiting a typed expiry DATE (fallback when the image can't be
#    read);
#  - _PD_PHOTO_LINK: the stored photo id to attach once that typed date arrives.
_PD_PHOTO_PENDING: dict[int, str] = {}
_PD_PHOTO_REVIEW: dict[int, dict] = {}
_PD_PENDING: dict[int, str] = {}
_PD_PHOTO_LINK: dict[int, int] = {}

# All quick-menu labels — a menu tap cancels a pending date entry (no trap).
_MENU_LABELS = frozenset(
    {
        _REMIND_MENU_LABEL,
        _EOD_MENU_LABEL,
        "📋 Bugungi reja",
        "📅 Kalendar",
        "📆 Muhim sanalar",
        _PD_MENU_LABEL,
        "💰 Qarzlar",
        "📓 Qarorlarim",
        "📰 Yangiliklar",
    }
)

# "Menga eslat" submenu copy.
_REMIND_INTRO = (
    "⏰ <b>Menga eslat</b>\n"
    "Yangi eslatma qo'shing yoki mavjudlarini ko'ring."
)
_REMIND_NEW_PROMPT = (
    "✍️ Eslatmani yozing — <b>nima</b> va <b>qachon</b>. Masalan:\n"
    "• «3 kundan keyin hujjatni yubor»\n"
    "• «ertaga soat 10 da Aliga qo'ng'iroq»\n"
    "• «har dushanba ertalab 8 da hisobot»\n"
    "• «oy oxirida to'lovni esla»"
)


async def _handle_remind_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle a ``mr:<action>`` tap: prompt for a new reminder, or list them."""
    data = query.data  # type: ignore[attr-defined]
    action = data.split(":", 1)[1] if ":" in data else ""
    message = query.message  # type: ignore[attr-defined]

    if action == "list":
        from app.brain.intent_router import RoutedIntent
        from app.brain.intents import ListReminders
        from app.services.dispatcher import dispatch

        result = await dispatch(
            registry,
            RoutedIntent("list_reminders", ListReminders(), {}),
            now=datetime.now(UTC),
        )
        await query.answer()  # type: ignore[attr-defined]
        if message is not None:
            await message.reply_text(result.text, parse_mode=result.parse_mode)
        return
    # action == "new" (or anything else): show the how-to prompt.
    await query.answer()  # type: ignore[attr-defined]
    if message is not None:
        await message.reply_text(_REMIND_NEW_PROMPT, parse_mode="HTML")


async def _handle_outbound_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle an ``out:<mode>`` tap: deliver the pending message by voice or text."""
    from app.db.models.enums import SendMode

    data = query.data  # type: ignore[attr-defined]
    mode_str = data.split(":", 1)[1] if ":" in data else "text"
    mode = SendMode.voice if mode_str == "voice" else SendMode.text
    await query.answer("⏳ Yuborilmoqda…")  # type: ignore[attr-defined]
    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, mode
    )
    message = query.message  # type: ignore[attr-defined]
    if message is None:
        return
    # Replace the «Qanday yuboray?» prompt with the outcome (buttons removed).
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )
    except Exception:  # noqa: BLE001 — uneditable/unchanged: post a fresh reply
        await message.reply_text(
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )


async def _handle_tday_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle a ``tday:<code>`` tap: set the chosen day, then finalize or ask the clock."""
    data = query.data  # type: ignore[attr-defined]
    code = data.split(":", 1)[1] if ":" in data else ""
    await query.answer("⏳ Belgilanmoqda…")  # type: ignore[attr-defined]
    result = await resume_time_day(registry, code, now=datetime.now(UTC))
    message = query.message  # type: ignore[attr-defined]
    if message is None:
        return
    if result is None:
        # Nothing pending anymore — drop the now-stale day buttons.
        try:
            await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        return
    # Replace the day prompt with the next step (clock/date prompt or the result).
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )
    except Exception:  # noqa: BLE001 — uneditable/unchanged: post a fresh reply
        await message.reply_text(
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )


async def _handle_paid_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle a ``paid:<id>:<dir>`` tap: settle that debt, then refresh the list."""
    data = query.data  # type: ignore[attr-defined]
    parts = data.split(":")
    if len(parts) < 3 or not parts[1].isdigit():
        await query.answer()  # type: ignore[attr-defined]
        return
    record_id, dir_code = int(parts[1]), parts[2]
    toast, result = await settle_debt(
        registry, record_id, dir_code, now=datetime.now(UTC)
    )
    await query.answer(toast)  # type: ignore[attr-defined]
    message = query.message  # type: ignore[attr-defined]
    if message is None:
        return
    # Replace the open-debts list with the refreshed view (the settled row is gone).
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )
    except Exception:  # noqa: BLE001 — uneditable/unchanged: post a fresh reply
        await message.reply_text(
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )


async def _handle_pick_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle a ``pick:<person_id>`` tap: resume the paused action with that contact."""
    data = query.data  # type: ignore[attr-defined]
    raw = data.split(":", 1)[1] if ":" in data else ""
    if not raw.isdigit():
        await query.answer()  # type: ignore[attr-defined]
        return
    await query.answer("⏳ Tanlanmoqda…")  # type: ignore[attr-defined]
    result = await resume_choice_pid(registry, int(raw), now=datetime.now(UTC))
    message = query.message  # type: ignore[attr-defined]
    if message is None:
        return
    if result is None:
        # Nothing pending anymore — just drop the now-stale pick buttons.
        try:
            await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        return
    # Replace the numbered list with the next step (voice/text prompt or result).
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )
    except Exception:  # noqa: BLE001 — uneditable/unchanged: post a fresh reply
        await message.reply_text(
            result.text,
            parse_mode=result.parse_mode,
            reply_markup=result.reply_markup,
        )


# ── end-of-day review ("Kun yakuni") ──────────────────────────────────────────
async def _eod_apply(
    registry: ServiceRegistry,
    kind: str,
    item_id: int,
    op: str,
    target: datetime | None = None,
) -> None:
    """Apply one end-of-day operation (done / move / cancel) to an item."""
    from app.bot.keyboards import KIND_PROMISE, KIND_REMINDER, KIND_TASK

    if kind == KIND_REMINDER:
        if op == "done":
            await registry.reminder_service.mark_done(item_id)
        elif op == "move" and target is not None:
            await registry.reminder_service.snooze(item_id, target)
        elif op == "cancel":
            await registry.reminder_service.cancel(item_id)
    elif kind in (KIND_PROMISE, KIND_TASK):
        if op == "done":
            await registry.task_service.mark_done(item_id)
        elif op == "move" and target is not None:
            await registry.task_service.reschedule(item_id, target)
        elif op == "cancel":
            await registry.task_service.cancel(item_id)


async def _eod_edit(query: object, text: str, kb: object | None) -> None:
    """Edit the end-of-day message (HTML), falling back to markup-only on error."""
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            text, parse_mode="HTML", reply_markup=kb
        )
    except Exception:  # noqa: BLE001 — unchanged text / uneditable
        try:
            await query.edit_message_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


async def _handle_eod_callback(registry: ServiceRegistry, query: object) -> None:
    """Drive the interactive end-of-day checklist (done / finish / tomorrow / delete)."""
    from app.bot.keyboards import eod_leftover_buttons
    from app.services._timeutil import snooze_target

    parts = query.data.split(":")  # type: ignore[attr-defined]
    action = parts[1] if len(parts) > 1 else ""
    now = datetime.now(UTC)
    briefing = registry.briefing_service
    if briefing is None:
        await query.answer()  # type: ignore[attr-defined]
        return

    # Tap a checklist item -> mark it done, then re-render the live checklist.
    if action == "done" and len(parts) == 4:
        await _eod_apply(registry, parts[2], int(parts[3]), "done")
        await query.answer("✅ Bajarildi")  # type: ignore[attr-defined]
        text, kb = await briefing.eod_message(now)
        if text is not None:
            await _eod_edit(query, text, kb)
        return

    # Done selecting -> ask about the leftovers.
    if action == "finish":
        items = await briefing.collect_eod(now)
        await query.answer()  # type: ignore[attr-defined]
        if not items:
            await _eod_edit(
                query, "🌙 <b>Kun yakuni</b>\n\n🎉 Barakalla — hammasi bajarildi!", None
            )
            return
        lines = "\n".join(f"• {_html_escape(t)}" for _k, _i, t in items)
        text = (
            "🌙 <b>Kun yakuni</b>\n\n"
            f"⌛ <b>Bajarilmaganlar ({len(items)}):</b>\n{lines}\n\n"
            "Ularni <b>ertaga eslataymi?</b>"
        )
        await _eod_edit(query, text, eod_leftover_buttons())
        return

    # Move all leftovers to tomorrow, or delete them.
    if action in ("tmrw", "del"):
        items = await briefing.collect_eod(now)
        if action == "tmrw":
            target = snooze_target(now, "tmrw", registry.settings.user_timezone)
            for kind, item_id, _t in items:
                await _eod_apply(registry, kind, item_id, "move", target)
            await query.answer("➡️ Ertaga ko'chirildi")  # type: ignore[attr-defined]
            await _eod_edit(
                query,
                f"➡️ <b>{len(items)} ta ish ertangi rejaga qo'shildi.</b>",
                None,
            )
        else:
            for kind, item_id, _t in items:
                await _eod_apply(registry, kind, item_id, "cancel")
            await query.answer("🗑 O'chirildi")  # type: ignore[attr-defined]
            await _eod_edit(
                query, f"🗑 <b>{len(items)} ta bajarilmagan ish o'chirildi.</b>", None
            )
        return
    await query.answer()  # type: ignore[attr-defined]


def _html_escape(text: str) -> str:
    import html as _html

    return _html.escape(text, quote=False)


def _pd_confirmation(event: object) -> str:
    """Build the 'date saved' confirmation with a friendly countdown."""
    date_str = event.event_date.strftime("%d.%m.%Y")  # type: ignore[attr-defined]
    left = ""
    next_fire = getattr(event, "next_fire_at", None)
    if next_fire is not None:
        from app.services._timeutil import as_utc

        days = (as_utc(next_fire) - datetime.now(UTC)).days
        if days >= 365:
            left = f" (~{days // 365} yil)"
        elif days >= 0:
            left = f" ({days} kun qoldi)"
    remind = ", ".join(
        str(d)
        for d in sorted(getattr(event, "remind_days_before", None) or [], reverse=True)
    )
    title = getattr(event, "title", "Sana")
    return (
        f"✅ Saqlandi: {title}\n"
        f"📅 Sana: {date_str}{left}\n"
        f"🔔 {remind} kun oldin eslataman."
    )


async def _handle_pd_date(
    registry: ServiceRegistry, message: Message, text: str
) -> None:
    """Fallback: the owner types the expiry date when the image was unreadable.

    Reuses the same expiry-event path as the photo flow (7/3/1-day alerts) and
    links the already-stored photo, so retrieval and reminders both work.
    """
    from app.bot.keyboards import KIND_EVENT, undo_button
    from app.brain.time_parse import parse_date
    from app.repositories import document_repo

    owner_key = registry.settings.owner_chat_id
    kind = _PD_PENDING.get(owner_key)
    if kind is None:
        return
    parsed = parse_date(text)
    if parsed is None:
        await message.reply_text(
            "📅 Sana noto'g'ri. KK.OO.YYYY ko'rinishida yozing (masalan 10.06.2027)."
        )
        return  # keep pending so the owner can retry
    _PD_PENDING.pop(owner_key, None)
    photo_id = _PD_PHOTO_LINK.pop(owner_key, None)

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        await message.reply_text("Egasi topilmadi. Avval /start yuboring.")
        return
    event = await registry.event_service.add_document_event(
        owner_id=owner_id, kind=kind, expiry=parsed
    )
    if photo_id is not None:
        async with registry.session() as session:
            await document_repo.set_event_id(session, photo_id, event.id)
    await message.reply_text(
        _pd_confirmation(event),
        parse_mode="HTML",
        reply_markup=undo_button(KIND_EVENT, event.id),
    )


async def _send_saved_documents(
    registry: ServiceRegistry, message: Message | None
) -> None:
    """Re-send each stored document photo with a kind + expiry caption."""
    if message is None:
        return
    from app.repositories import document_repo, event_repo

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            await message.reply_text("Egasi topilmadi. Avval /start yuboring.")
            return
        photos = await document_repo.list_latest_per_kind(session, owner.id)
        items: list[tuple[str, str]] = []
        for ph in photos:
            caption = _DOC_LABELS.get(ph.kind, "📄 Hujjat")
            if ph.event_id is not None:
                ev = await event_repo.get(session, ph.event_id)
                if ev is not None:
                    caption += f" — {ev.event_date.strftime('%d.%m.%Y')} gacha"
            items.append((ph.file_id, caption))

    if not items:
        await message.reply_text(
            "📭 Hali hujjat saqlanmagan. «🪪 Ma'lumotlarim»dan turini tanlab "
            "rasm yuboring."
        )
        return
    for file_id, caption in items:
        try:
            await message.reply_photo(file_id, caption=caption)
        except Exception:  # noqa: BLE001 — a stale file_id shouldn't abort the rest
            await message.reply_text(f"{caption}\n(rasmni yuborib bo'lmadi)")


async def _handle_pd_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle a ``pd:<action>`` submenu tap (capture a document, or view saved)."""
    data = query.data  # type: ignore[attr-defined]
    action = data.split(":", 1)[1] if ":" in data else ""
    owner_key = registry.settings.owner_chat_id
    message = query.message  # type: ignore[attr-defined]

    if action == "list":
        from app.brain.intent_router import RoutedIntent
        from app.brain.intents import ListImportantDates
        from app.services.dispatcher import dispatch

        result = await dispatch(
            registry,
            RoutedIntent("list_important_dates", ListImportantDates(), {}),
            now=datetime.now(UTC),
        )
        await query.answer()  # type: ignore[attr-defined]
        if message is not None:
            await message.reply_text(result.text, parse_mode=result.parse_mode)
        return

    if action == "photos":
        await query.answer()  # type: ignore[attr-defined]
        await _send_saved_documents(registry, message)
        return

    if action in _PD_PROMPTS:
        from app.repositories import document_repo

        await query.answer()  # type: ignore[attr-defined]
        # If one is already saved, show it with manage buttons; else ask for a photo.
        async with registry.session() as session:
            owner = await person_repo.get_owner(session)
            existing = (
                await document_repo.latest_by_kind(session, owner.id, action)
                if owner is not None
                else None
            )
        if existing is not None:
            await _send_saved_one(registry, message, existing, action)
            return
        _PD_PHOTO_PENDING[owner_key] = action
        _PD_PENDING.pop(owner_key, None)
        _PD_PHOTO_LINK.pop(owner_key, None)
        if message is not None:
            await message.reply_text(_PD_PROMPTS[action], parse_mode="HTML")
        return
    await query.answer()  # type: ignore[attr-defined]


_NO_NLU = (
    "Sun'iy intellekt hozircha sozlanmagan (AI kaliti topilmadi). "
    "Provayderga qarab GEMINI_API_KEY yoki ANTHROPIC_API_KEY ni .env faylga "
    "qo'shing va botni qayta ishga tushiring."
)

_NO_STT = (
    "Ovozli xabarlarni tushunish sozlanmagan (GEMINI_API_KEY yoki "
    "ELEVENLABS_API_KEY kerak). Iltimos, matn ko'rinishida yozing."
)

_STT_FAILED = "Ovozni tushunolmadim. Iltimos, qaytadan, aniqroq yuboring."

# Shown when the owner tries to act before confirming the morning plan.
_MORNING_GATE_MSG = (
    "🌅 Avval bugungi rejani tasdiqlang — yuqoridagi postdagi «✅ Tasdiqlash» "
    "tugmasini bosing. Shundan keyin boshqa ishlarni bajarsa bo'ladi."
)

_GENERIC_ERROR = "Xatolik yuz berdi. Iltimos, biroz o'zgartirib qaytadan urinib ko'ring."

_ERR_NO_CREDIT = (
    "AI miyasi ishlamayapti: Anthropic hisobingizda kredit tugagan. "
    "console.anthropic.com → Plans & Billing dan kredit to'ldiring."
)
_ERR_AUTH = (
    "AI miyasi ishlamayapti: ANTHROPIC_API_KEY noto'g'ri yoki bekor qilingan. "
    ".env faylidagi kalitni tekshiring."
)
_ERR_RATE = (
    "AI so'rovlar limitiga yetdi. Biroz kuting va qayta urinib ko'ring "
    "(tekin kunlik limit ertaga yangilanadi)."
)
_ERR_RATE_DAILY = (
    "⛔️ AI kunlik so'rov limiti tugadi.\n"
    "Tekin kunlik chegara ertaga (~soat 12:00, Toshkent) yangilanadi."
)
_ERR_OVERLOADED = "AI hozir juda band. Iltimos, bir oz kuting va qayta urinib ko'ring."

# Live countdown tasks are kept here so they are not garbage-collected mid-tick.
_BG_TASKS: set[asyncio.Task] = set()

# Hard cap on a whole command (NLU + dispatch + TTS + send) so a stalled API
# call can never leave the loading message stuck forever.
_RESPOND_TIMEOUT = 90.0


def _error_reply(exc: Exception) -> str:
    """Map an NLU/dispatch failure to a clear Uzbek message for the owner.

    Provider failures (quota/rate, no credit, bad key, overload) are surfaced
    specifically so the owner can fix the real cause instead of rephrasing.
    """
    msg = str(exc).lower()
    name = type(exc).__name__
    # Quota / rate limit FIRST: Gemini's 429 message also contains the word
    # "billing", which must not be misread as an Anthropic credit problem.
    if (
        name == "RateLimitError"
        or "resource_exhausted" in msg
        or "quota" in msg
        or "rate limit" in msg
    ):
        return _ERR_RATE
    if "credit balance" in msg:  # Anthropic-specific phrasing
        return _ERR_NO_CREDIT
    if (
        name == "AuthenticationError"
        or "authentication" in msg
        or "invalid x-api-key" in msg
        or "api key not valid" in msg
        or "permission_denied" in msg
    ):
        return _ERR_AUTH
    if (
        name in ("InternalServerError", "ServerError")
        or "overloaded" in msg
        or "unavailable" in msg
        or "high demand" in msg
        or "503" in msg
    ):
        return _ERR_OVERLOADED
    return _GENERIC_ERROR


def _is_rate_limit(exc: Exception) -> bool:
    """True when the failure is an AI request-quota / rate-limit error."""
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        name == "RateLimitError"
        or "resource_exhausted" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "429" in msg
    )


def _is_daily_limit(exc: Exception) -> bool:
    """True when the exhausted quota is a per-DAY one (not per-minute)."""
    s = str(exc).lower()
    return "perday" in s or "per day" in s or "per_day" in s or "daily" in s


def _retry_after_seconds(exc: Exception) -> int | None:
    """Pull the provider's suggested retry delay (e.g. 'retryDelay': '41s')."""
    match = re.search(r"retrydelay['\"]?\s*[:=]\s*['\"]?(\d+)\s*s", str(exc).lower())
    return int(match.group(1)) if match else None


def _fmt_dur(seconds: int) -> str:
    """Human countdown text: '45 soniya' or '2:05' (m:ss)."""
    if seconds < 60:
        return f"{seconds} soniya"
    return f"{seconds // 60}:{seconds % 60:02d}"


async def _safe_edit(
    message: Message,
    text: str,
    parse_mode: str | None = None,
    reply_markup: object | None = None,
) -> None:
    """Edit a message; on HTML/parse failure retry as tag-stripped plain text."""
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:  # noqa: BLE001 — malformed HTML / unchanged / send error
        try:
            await message.edit_text(
                re.sub(r"<[^>]+>", "", text), reply_markup=reply_markup
            )
        except Exception:  # noqa: BLE001
            pass


async def _rate_limit_on(message: Message, exc: Exception) -> None:
    """Turn ``message`` into a live countdown to when the AI limit reopens.

    Per-minute limits tick the message down to zero, then to 'try again'; a
    per-day limit shows the next reset time (no live tick).
    """
    if _is_daily_limit(exc):
        await _safe_edit(message, _ERR_RATE_DAILY)
        return
    seconds = min(_retry_after_seconds(exc) or 60, 600)
    target = datetime.now(UTC) + timedelta(seconds=seconds)
    base = "⏳ AI so'rovlar limiti. Yangilanishiga:"
    await _safe_edit(message, f"{base} {_fmt_dur(seconds)}")

    async def _tick() -> None:
        step = 10 if seconds > 30 else 5
        while True:
            await asyncio.sleep(step)
            remaining = int((target - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                break
            try:
                await message.edit_text(f"{base} {_fmt_dur(remaining)}")
            except Exception:  # noqa: BLE001 — edit may fail (unchanged / limits)
                return
        try:
            await message.edit_text("✅ Limit tiklandi — endi qayta urinib ko'ring.")
        except Exception:  # noqa: BLE001
            pass

    task = asyncio.create_task(_tick())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _respond(message: Message, run, *, loading: str = "⏳ Bajarilmoqda…") -> None:
    """Show a loading placeholder, run ``run()``, then edit it with the result.

    Every command gets a tidy loading "skeleton"; rate-limit errors turn the
    same message into a live countdown, other errors into a clear note. ``run``
    is a zero-arg coroutine factory returning a ``DispatchResult`` (or ``None``).
    """
    placeholder = await message.reply_text(loading)
    try:
        result = await asyncio.wait_for(run(), timeout=_RESPOND_TIMEOUT)
    except TimeoutError:
        logger.warning("bot.respond.timeout")
        await _safe_edit(
            placeholder,
            "⏳ Biroz sekin ketdi (AI javob bermadi). Qayta urinib ko'ring.",
        )
        return
    except Exception as exc:  # noqa: BLE001 — a failure must never crash the loop
        logger.exception("bot.respond.failed")
        if _is_rate_limit(exc):
            await _rate_limit_on(placeholder, exc)
        else:
            await _safe_edit(placeholder, _error_reply(exc))
        return
    if result is None:
        await _safe_edit(placeholder, _GENERIC_ERROR)
        return
    await _safe_edit(
        placeholder, result.text, result.parse_mode, result.reply_markup
    )


def _registry(context: ContextTypes.DEFAULT_TYPE) -> ServiceRegistry:
    """Pull the live registry stashed in ``bot_data`` at build time."""
    return context.application.bot_data["registry"]


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/start`` — make sure the owner record exists, then greet."""
    registry = _registry(context)
    async with registry.session() as session:
        await person_repo.ensure_owner(
            session,
            telegram_user_id=registry.settings.owner_chat_id,
            display_name="Owner",
        )
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            GREETING, reply_markup=_MENU_KEYBOARD, parse_mode="HTML"
        )


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/help`` — show a short, example-led usage guide."""
    if update.effective_message is not None:
        await update.effective_message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def _morning_gate_blocks(registry: ServiceRegistry, message: Message) -> bool:
    """If the morning plan is awaiting confirmation, tell the owner and block."""
    briefing = registry.briefing_service
    if briefing is not None and await briefing.is_morning_pending():
        await message.reply_text(_MORNING_GATE_MSG)
        return True
    return False


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a free-form owner text message."""
    message = update.effective_message
    if message is None or not message.text:
        return
    text = message.text.strip()
    if not text:
        return
    registry = _registry(context)
    # Gate: nothing runs until today's morning plan is confirmed.
    if await _morning_gate_blocks(registry, message):
        return

    owner_key = registry.settings.owner_chat_id
    # Tapping any menu button abandons a half-started compose / time prompt.
    if text in _MENU_LABELS:
        clear_pending_compose(owner_key)
        clear_pending_time(owner_key)
    # End-of-day review: deliver the interactive checklist.
    if text == _EOD_MENU_LABEL:
        if registry.briefing_service is not None:
            await registry.briefing_service.run_evening()
        return
    # Open the "Menga eslat" submenu.
    if text == _REMIND_MENU_LABEL:
        from app.bot.keyboards import remind_menu

        await message.reply_text(
            _REMIND_INTRO, reply_markup=remind_menu(), parse_mode="HTML"
        )
        return
    # Open the personal-data submenu.
    if text == _PD_MENU_LABEL:
        from app.bot.keyboards import personal_data_menu

        await message.reply_text(
            _PD_INTRO, reply_markup=personal_data_menu(), parse_mode="HTML"
        )
        return
    # Awaiting a personal-data date? Treat this as the date — unless the owner
    # tapped another menu button (then cancel and handle normally).
    if owner_key in _PD_PENDING:
        if text in _MENU_LABELS:
            _PD_PENDING.pop(owner_key, None)
        else:
            await _handle_pd_date(registry, message, text)
            return

    await _route_and_reply(registry, message, text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe an owner voice/audio message, then treat it as text."""
    registry = _registry(context)
    message = update.effective_message
    if message is None:
        return
    media = message.voice or message.audio
    if media is None:
        return

    # Gate: block voice commands too until the morning plan is confirmed.
    if await _morning_gate_blocks(registry, message):
        return

    voice = registry.voice_service
    if voice is None or not voice.can_transcribe():
        await message.reply_text(_NO_STT)
        return

    await message.chat.send_action(ChatAction.TYPING)
    text = await _transcribe_message(registry, message, media)
    if not text:
        await message.reply_text(_STT_FAILED)
        return

    # Echo what we heard so the owner can catch a mis-hearing, then act on it.
    await message.reply_text(f"🎙 «{text}»")
    await _route_and_reply(registry, message, text)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a document photo sent after the owner tapped a document button."""
    registry = _registry(context)
    message = update.effective_message
    if message is None or not message.photo:
        return
    if await _morning_gate_blocks(registry, message):
        return

    owner_key = registry.settings.owner_chat_id
    kind = _PD_PHOTO_PENDING.get(owner_key)
    if kind is None:
        # A stray photo with no pending capture: gently point to the menu.
        await message.reply_text(
            "📸 Hujjat saqlash uchun avval «🪪 Ma'lumotlarim»dan turini tanlang."
        )
        return
    _PD_PHOTO_PENDING.pop(owner_key, None)
    await _handle_document_photo(registry, message, kind)


async def _handle_document_photo(
    registry: ServiceRegistry, message: Message, kind: str
) -> None:
    """Read the expiry from the photo, then show Save/Cancel before storing it.

    Nothing is written to the DB yet — the upload is held in ``_PD_PHOTO_REVIEW``
    until the owner taps «✅ Saqlash» (or discards it with «❌ Bekor qilish»).
    """
    from app.bot.keyboards import doc_review_keyboard

    photo = message.photo[-1]  # the highest-resolution size
    await message.chat.send_action(ChatAction.TYPING)
    expiry = None
    doc = registry.document_service
    if doc is not None and doc.available():
        try:
            image_bytes = await _download_photo_bytes(photo)
            expiry = await doc.extract_expiry(image_bytes, mime_type="image/jpeg")
        except Exception as exc:  # noqa: BLE001 — fall back to asking for the date
            logger.warning("bot.document.extract_failed", error=str(exc))

    _PD_PHOTO_REVIEW[registry.settings.owner_chat_id] = {
        "kind": kind,
        "file_id": photo.file_id,
        "expiry": expiry,
    }
    label = _DOC_LABELS.get(kind, "📄 Hujjat")
    if expiry is not None:
        body = (
            f"📸 <b>{label}</b> qabul qilindi.\n"
            f"📅 Amal qilish muddati: <b>{expiry.strftime('%d.%m.%Y')}</b> (o'qildi).\n\n"
            "Saqlaymi?"
        )
    else:
        body = (
            f"📸 <b>{label}</b> qabul qilindi.\n"
            "📅 Muddatni rasmdan o'qiy olmadim — saqlagandan keyin qo'lda yozasiz.\n\n"
            "Saqlaymi?"
        )
    await message.reply_text(
        body, parse_mode="HTML", reply_markup=doc_review_keyboard()
    )


async def _finish_doc(query: object, text: str, parse_mode: str | None = None) -> None:
    """Finalize a doc action: drop the buttons, then show the outcome.

    Works whether the card is a text message (edited in place) or a photo message
    (its markup is stripped and a fresh reply carries the outcome).
    """
    try:
        await query.edit_message_text(text, parse_mode=parse_mode)  # type: ignore[attr-defined]
        return
    except Exception:  # noqa: BLE001 — a photo/caption card can't take edit_message_text
        pass
    try:
        await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — already gone / unchanged
        pass
    message = query.message  # type: ignore[attr-defined]
    if message is not None:
        await message.reply_text(text, parse_mode=parse_mode)


async def _handle_doc_callback(registry: ServiceRegistry, query: object) -> None:
    """Handle ``doc:save`` / ``doc:cancel`` / ``doc:new:<kind>`` / ``doc:del:<kind>``."""
    parts = query.data.split(":")  # type: ignore[attr-defined]
    action = parts[1] if len(parts) > 1 else ""
    owner_key = registry.settings.owner_chat_id
    message = query.message  # type: ignore[attr-defined]

    if action == "cancel":
        _PD_PHOTO_REVIEW.pop(owner_key, None)
        await query.answer("Bekor qilindi")  # type: ignore[attr-defined]
        await _finish_doc(query, "❌ Bekor qilindi. Rasm saqlanmadi.")
        return

    if action == "save":
        await query.answer("⏳ Saqlanmoqda…")  # type: ignore[attr-defined]
        await _doc_save(registry, query)
        return

    if action == "new" and len(parts) > 2:
        kind = parts[2]
        _PD_PHOTO_PENDING[owner_key] = kind
        _PD_PHOTO_REVIEW.pop(owner_key, None)
        _PD_PENDING.pop(owner_key, None)
        _PD_PHOTO_LINK.pop(owner_key, None)
        await query.answer()  # type: ignore[attr-defined]
        if message is not None:
            await message.reply_text(
                _PD_PROMPTS.get(kind, "📸 Rasmni yuboring."), parse_mode="HTML"
            )
        return

    if action == "del" and len(parts) > 2:
        await query.answer("⏳ O'chirilmoqda…")  # type: ignore[attr-defined]
        await _doc_delete(registry, query, parts[2])
        return

    await query.answer()  # type: ignore[attr-defined]


async def _doc_save(registry: ServiceRegistry, query: object) -> None:
    """Store the reviewed photo (replacing any prior one of the same kind)."""
    from app.repositories import document_repo

    owner_key = registry.settings.owner_chat_id
    review = _PD_PHOTO_REVIEW.pop(owner_key, None)
    if review is None:
        await _finish_doc(query, "Bu so'rov eskirgan. Rasmni qaytadan yuboring.")
        return
    kind, file_id, expiry = review["kind"], review["file_id"], review["expiry"]

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        await _finish_doc(query, "Egasi topilmadi. Avval /start yuboring.")
        return

    # Replace semantics: drop any previous photo + alerts of this kind first.
    async with registry.session() as session:
        old_event_ids = await document_repo.delete_by_kind(session, owner_id, kind)
    for eid in old_event_ids:
        await registry.event_service.cancel(eid)

    async with registry.session() as session:
        saved = await document_repo.create(
            session, owner_id=owner_id, kind=kind, file_id=file_id
        )
        photo_id = saved.id

    if expiry is None:
        # Saved the image; now ask the owner to type the unreadable date.
        _PD_PENDING[owner_key] = kind
        _PD_PHOTO_LINK[owner_key] = photo_id
        await _finish_doc(
            query,
            "📸 Saqlandi. Endi amal qilish muddatini yozing: KK.OO.YYYY "
            "(masalan 10.06.2027).",
        )
        return

    event = await registry.event_service.add_document_event(
        owner_id=owner_id, kind=kind, expiry=expiry
    )
    async with registry.session() as session:
        await document_repo.set_event_id(session, photo_id, event.id)
    await _finish_doc(query, _pd_confirmation(event), parse_mode="HTML")


async def _doc_delete(
    registry: ServiceRegistry, query: object, kind: str
) -> None:
    """Delete the saved document of ``kind`` and cancel its expiry alerts."""
    from app.repositories import document_repo

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        await _finish_doc(query, "Egasi topilmadi.")
        return
    async with registry.session() as session:
        event_ids = await document_repo.delete_by_kind(session, owner_id, kind)
    for eid in event_ids:
        await registry.event_service.cancel(eid)
    label = _DOC_LABELS.get(kind, "Hujjat")
    await _finish_doc(query, f"🗑 {label} o'chirildi.")


async def _send_saved_one(
    registry: ServiceRegistry, message: Message | None, photo_row: object, kind: str
) -> None:
    """Show one saved document with «🔄 Yangisini yuklash | 🗑 O'chirish» buttons."""
    if message is None:
        return
    from app.bot.keyboards import doc_manage_keyboard
    from app.repositories import event_repo

    caption = _DOC_LABELS.get(kind, "📄 Hujjat")
    event_id = getattr(photo_row, "event_id", None)
    if event_id is not None:
        async with registry.session() as session:
            ev = await event_repo.get(session, event_id)
            if ev is not None:
                caption += f" — {ev.event_date.strftime('%d.%m.%Y')} gacha"
    caption += "\n\nO'zgartirmoqchimisiz?"
    file_id = getattr(photo_row, "file_id", "")
    try:
        await message.reply_photo(
            file_id, caption=caption, reply_markup=doc_manage_keyboard(kind)
        )
    except Exception:  # noqa: BLE001 — stale file_id: still offer the actions
        await message.reply_text(caption, reply_markup=doc_manage_keyboard(kind))


async def _download_photo_bytes(photo: object) -> bytes:
    """Download a Telegram ``PhotoSize`` to raw bytes for vision analysis."""
    tg_file = await photo.get_file()  # type: ignore[attr-defined]
    return bytes(await tg_file.download_as_bytearray())


# ── contact-pick parsing ──────────────────────────────────────────────────────
_ORDINAL_WORDS = {
    "birinchi": 1,
    "ikkinchi": 2,
    "uchinchi": 3,
    "to'rtinchi": 4,
    "toʻrtinchi": 4,
    "beshinchi": 5,
}


def _parse_selection(text: str) -> int | None:
    """Parse a bare contact-pick reply ('1', '2-chi', 'birinchi') to an index.

    Returns ``None`` unless the WHOLE message is just a selection, so a real
    command that merely contains a number is never mistaken for a pick.
    """
    t = text.strip().lower()
    match = re.fullmatch(r"(\d{1,2})\s*[-.)]?\s*(?:chi|inchi|nchi)?", t)
    if match:
        return int(match.group(1))
    return _ORDINAL_WORDS.get(t)


# ── shared pipeline ───────────────────────────────────────────────────────────
async def _route_and_reply(
    registry: ServiceRegistry, message: Message, text: str
) -> None:
    """Route ``text`` to an action and reply with a loading→result flow.

    Order: a bare number answers a pending "which contact?" prompt; a quick-menu
    button is dispatched directly (no NLU, no quota); anything else goes through
    the NLU brain. Each path shows a loading placeholder that is then edited with
    the result (or a live countdown / error).
    """
    owner_key = registry.settings.owner_chat_id

    # A scheduling action is waiting for the owner to TYPE the time/date (the
    # day was/will be picked via buttons). Capture this message as that answer.
    if has_pending_time(owner_key):
        if _menu_intent(text) is not None or text in _MENU_LABELS:
            clear_pending_time(owner_key)
        else:
            await _respond(
                message,
                lambda: resume_time_text(registry, text, now=datetime.now(UTC)),
                loading="⏳ Belgilanmoqda…",
            )
            return

    # A contact was just picked from a "show contacts" list — this message is the
    # body to send it (unless the owner tapped a menu button, which cancels).
    if has_pending_compose(owner_key):
        if _menu_intent(text) is not None or text in _MENU_LABELS:
            clear_pending_compose(owner_key)
        else:
            await _respond(
                message,
                lambda: dispatch_compose(registry, text, now=datetime.now(UTC)),
                loading="⏳ Tayyorlanmoqda…",
            )
            return

    selection = _parse_selection(text)
    if has_pending(owner_key):
        # A bare number answers the "which contact?" prompt directly.
        if selection is not None:
            await _respond(
                message,
                lambda: resume_choice(registry, selection, now=datetime.now(UTC)),
                loading="⏳ Tanlanmoqda…",
            )
            return
        # A non-number reply mid-pick is either a corrected contact name (reuse
        # the paused message/time) or a brand-new command. Route it, then let the
        # dispatcher decide — so "Doniyor aka og'am ga" resumes the same task
        # instead of falling through to a puzzled "Tushunmadim".
        nlu = registry.nlu_service

        async def _pending_or_new() -> object:
            now = datetime.now(UTC)
            if nlu is None or not nlu.available():
                routed = RoutedIntent("unknown", None, {})
            else:
                now_iso = now.astimezone(
                    ZoneInfo(registry.settings.user_timezone)
                ).isoformat()
                routed = await nlu.route(text, now_iso=now_iso)
            return await resume_with_correction(
                registry, routed, raw_text=text, now=now
            )

        await _respond(message, _pending_or_new, loading="⏳ Bajarilmoqda…")
        return

    # No pending pick: a fresh message supersedes any waiting voice/text choice.
    clear_pending_outbound(owner_key)

    menu = _menu_intent(text)
    if menu is not None:
        await _respond(
            message,
            lambda: dispatch(registry, menu, now=datetime.now(UTC)),
            loading=_MENU_LOADING.get(text, "⏳ Bajarilmoqda…"),
        )
        return

    nlu = registry.nlu_service
    if nlu is None or not nlu.available():
        await message.reply_text(_NO_NLU)
        return

    async def _route() -> object:
        now = datetime.now(UTC)
        now_iso = now.astimezone(
            ZoneInfo(registry.settings.user_timezone)
        ).isoformat()
        routed = await nlu.route(text, now_iso=now_iso)
        return await dispatch(registry, routed, now=now)

    await _respond(message, _route, loading="⏳ Bajarilmoqda…")


async def _transcribe_message(
    registry: ServiceRegistry, message: Message, media: object
) -> str:
    """Download the voice/audio file and transcribe it to text (``""`` on fail)."""
    try:
        mime = getattr(media, "mime_type", None) or "audio/ogg"
        hint_names = await _contact_hint_names(registry)
        tg_file = await media.get_file()  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "voice.ogg")
            await tg_file.download_to_drive(path)
            return await registry.voice_service.transcribe(  # type: ignore[union-attr]
                path, mime_type=mime, hint_names=hint_names
            )
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on download/STT error
        logger.warning("bot.transcribe.failed", error=str(exc))
        return ""


# How many contact names to feed STT as spelling hints. A large synced phonebook
# is noisy, so instead of all-or-nothing we send a BOUNDED, prioritized slice:
# reachable Telegram contacts (the people the owner actually messages) first.
_STT_HINT_MAX = 200


async def _contact_hint_names(registry: ServiceRegistry) -> list[str]:
    """Owner's most relevant contact names, fed to STT for correct spelling."""
    try:
        async with registry.session() as session:
            people = await person_repo.list_all(session)
    except Exception as exc:  # noqa: BLE001 — names are a best-effort hint
        logger.warning("bot.contact_hints.failed", error=str(exc))
        return []
    # Bias the slice toward reachable contacts (those with a Telegram id); skip
    # the owner's own record. This keeps the hint useful even with thousands of
    # synced numbers, where a flat list would just be an arbitrary, noisy cut.
    reachable: list[str] = []
    others: list[str] = []
    for p in people:
        name = (p.display_name or "").strip()
        if not name or getattr(p, "is_owner", False):
            continue
        (reachable if p.telegram_user_id is not None else others).append(name)
    return (reachable + others)[:_STT_HINT_MAX]


# ── inline button callbacks ───────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an inline-button tap (Done / Snooze / Move / Cancel).

    Parses the compact callback payload, runs the matching service action, shows a
    short toast, and rewrites the original card to reflect the new state (buttons
    removed). Any failure degrades to a toast so polling never tears down.
    """
    query = update.callback_query
    if query is None or not query.data:
        return
    registry = _registry(context)
    # Owner-only: CallbackQueryHandler carries no chat filter, so guard here.
    if update.effective_chat is None or (
        update.effective_chat.id != registry.settings.owner_chat_id
    ):
        await query.answer()
        return
    from app.bot.keyboards import VERB_CONFIRM, parse_callback

    data = query.data
    # Gate: only the «✅ Tasdiqlash» tap is allowed until the plan is confirmed.
    briefing = registry.briefing_service
    if (
        not data.startswith(f"{VERB_CONFIRM}:")
        and briefing is not None
        and await briefing.is_morning_pending()
    ):
        await query.answer("🌅 Avval bugungi rejani tasdiqlang", show_alert=True)
        return
    # Submenu actions use simple ``pd:<action>`` / ``mr:<action>`` payloads.
    if data.startswith("pd:"):
        await _handle_pd_callback(registry, query)
        return
    if data.startswith("mr:"):
        await _handle_remind_callback(registry, query)
        return
    if data.startswith("out:"):
        await _handle_outbound_callback(registry, query)
        return
    if data.startswith("pick:"):
        await _handle_pick_callback(registry, query)
        return
    if data.startswith("paid:"):
        await _handle_paid_callback(registry, query)
        return
    if data.startswith("tday:"):
        await _handle_tday_callback(registry, query)
        return
    if data.startswith("doc:"):
        await _handle_doc_callback(registry, query)
        return
    if data.startswith("eod:"):
        await _handle_eod_callback(registry, query)
        return

    cb = parse_callback(data)
    if cb is None:
        await query.answer()
        return
    try:
        toast, status_line = await _apply_callback(registry, cb, datetime.now(UTC))
    except Exception:  # noqa: BLE001 — never let a callback crash the loop
        logger.exception("bot.callback.failed", data=query.data)
        await query.answer("Xatolik yuz berdi")
        return
    await query.answer(toast or None)
    if status_line and query.message is not None:
        await _finalize_card(query, status_line)


async def _apply_callback(
    registry: ServiceRegistry, cb: object, now: datetime
) -> tuple[str, str | None]:
    """Run the action a callback encodes; return ``(toast, status_line_html)``.

    ``status_line_html`` is appended to the original card to show the outcome
    (``None`` => leave the card text unchanged, just drop the buttons).
    """
    from app.bot.keyboards import (
        KIND_MEETING,
        KIND_PROMISE,
        KIND_REMINDER,
        KIND_TASK,
        VERB_ACK,
        VERB_CANCEL,
        VERB_CONFIRM,
        VERB_DONE,
        VERB_MOVE,
        VERB_SNOOZE,
        VERB_STOP,
    )
    from app.services._timeutil import snooze_target, to_local_str

    tz = registry.settings.user_timezone
    verb, kind, item_id, arg = cb.verb, cb.kind, cb.item_id, cb.arg  # type: ignore[attr-defined]

    if verb == VERB_CONFIRM:
        # Owner confirmed the morning plan -> lower the gate, unlock the bot.
        if registry.briefing_service is not None:
            await registry.briefing_service.clear_morning_pending()
        return "✅ Tasdiqlandi", "✅ <b>Reja tasdiqlandi — endi ish boshlasangiz bo'ladi</b>"

    if verb == VERB_DONE:
        if kind == KIND_REMINDER:
            await registry.reminder_service.mark_done(item_id)
        elif kind in (KIND_PROMISE, KIND_TASK):
            await registry.task_service.mark_done(item_id)
        return "✅ Bajarildi", "✅ <b>Bajarildi</b>"

    if verb == VERB_ACK:
        toast = "✅ Tushunarli" if kind == KIND_MEETING else "👍 Belgilandi"
        return toast, "✅ <i>Belgilandi</i>"

    if verb == VERB_SNOOZE:
        target = snooze_target(now, arg or "60", tz)
        if kind == KIND_REMINDER:
            await registry.reminder_service.snooze(item_id, target)
        elif kind in (KIND_PROMISE, KIND_TASK):
            await registry.task_service.snooze(item_id, target)
        label = to_local_str(target, tz)
        return f"⏰ {label}", f"⏰ <i>Keyinga qoldirildi: {label}</i>"

    if verb == VERB_MOVE:
        target = snooze_target(now, arg or "60", tz)
        if kind == KIND_MEETING:
            await registry.meeting_service.move(item_id, target)
        label = to_local_str(target, tz)
        return f"📅 {label}", f"📅 <i>Ko'chirildi: {label}</i>"

    if verb == VERB_STOP:
        if kind == KIND_REMINDER:
            await registry.reminder_service.cancel(item_id)
        return "🚫 To'xtatildi", "🚫 <i>Takror to'xtatildi</i>"

    if verb == VERB_CANCEL:
        ok = await _cancel_by_kind(registry, kind, item_id)
        if ok:
            return "❌ Bekor qilindi", "❌ <i>Bekor qilindi</i>"
        return "Topilmadi yoki allaqachon bajarilgan", None

    return "", None


async def _cancel_by_kind(registry: ServiceRegistry, kind: str, item_id: int) -> bool:
    """Cancel/undo a freshly created item by its kind code; ``True`` on success."""
    from app.bot.keyboards import (
        KIND_DECISION,
        KIND_EVENT,
        KIND_FINANCE,
        KIND_MEETING,
        KIND_MESSAGE,
        KIND_PROMISE,
        KIND_REMINDER,
        KIND_TASK,
    )

    if kind == KIND_REMINDER:
        await registry.reminder_service.cancel(item_id)
        return True
    if kind in (KIND_PROMISE, KIND_TASK):
        await registry.task_service.cancel(item_id)
        return True
    if kind == KIND_MEETING:
        return await registry.meeting_service.cancel(item_id)
    if kind == KIND_MESSAGE:
        return await registry.message_service.cancel(item_id)
    if kind == KIND_FINANCE:
        return await registry.finance_service.delete(item_id)
    if kind == KIND_EVENT:
        return await registry.event_service.cancel(item_id)
    if kind == KIND_DECISION:
        return await registry.decision_service.delete(item_id)
    return False


async def _finalize_card(query: object, status_line: str) -> None:
    """Append ``status_line`` to a tapped card and remove its buttons.

    Preserves the card's original HTML formatting; falls back to merely dropping
    the keyboard when the message can't be edited as text (e.g. a voice note).
    """
    message = query.message  # type: ignore[attr-defined]
    base = ""
    if getattr(message, "text", None) is not None:
        base = message.text_html
    elif getattr(message, "caption", None) is not None:
        base = message.caption_html
    new_text = f"{base}\n\n{status_line}" if base else status_line
    try:
        await query.edit_message_text(  # type: ignore[attr-defined]
            new_text, parse_mode="HTML", reply_markup=None
        )
    except Exception:  # noqa: BLE001 — uneditable media / unchanged text
        try:
            await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Last-resort error handler so a handler exception never tears down polling."""
    logger.exception("bot.unhandled_error", error=str(context.error))
