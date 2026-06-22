"""Inline keyboards + callback-data codec for the control bot.

Every actionable owner-facing message (a fired reminder, a meeting alert, a
creation confirmation, an evening-review leftover card) carries inline buttons so
the owner can act with one tap — «✅ Bajarildi», «⏰ Keyinroq», «❌ Bekor qilish» —
instead of typing a follow-up command. This module is the single source of truth
for what those buttons look like and how their callback payloads are encoded, so
the builders here and the parser in :func:`parse_callback` never drift apart.

Callback payload format (Telegram caps it at 64 bytes):
    ``verb:kind:id[:arg]``
e.g. ``done:rem:42``, ``snz:rem:42:60``, ``snz:prm:7:tmrw``, ``mv:mtg:3:tmrw``,
``stop:rem:9``, ``cancel:mtg:5``. ``arg`` is a minute count or the literal
``tmrw`` (tomorrow 09:00 local).

User-facing labels are Uzbek (latin); verbs/kinds/comments are English.
"""

from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_Btn = InlineKeyboardButton

# ── kind codes (short, to fit the 64-byte callback budget) ───────────────────
KIND_REMINDER = "rem"
KIND_PROMISE = "prm"
KIND_TASK = "tsk"        # delegated task (owner side)
KIND_MEETING = "mtg"
KIND_MESSAGE = "msg"     # scheduled (future) outbound message
KIND_FINANCE = "fin"
KIND_EVENT = "evt"       # important date / birthday
KIND_DECISION = "dec"    # decisions journal entry
KIND_BRIEFING = "brf"    # the morning plan (confirmation gate)

# ── verbs ────────────────────────────────────────────────────────────────────
VERB_DONE = "done"       # mark a one-shot item done (+ cancel its job)
VERB_ACK = "ack"         # acknowledge only, no state change (recurring/meeting)
VERB_SNOOZE = "snz"      # reschedule an item sooner (arg = minutes | tmrw)
VERB_MOVE = "mv"         # move a meeting / leftover to a later time
VERB_STOP = "stop"       # stop a recurring reminder entirely
VERB_CANCEL = "cancel"   # undo a freshly created item
VERB_CONFIRM = "ok"      # confirm/acknowledge the morning plan (clears the gate)

# Tomorrow-09:00 sentinel shared by snooze/move args.
ARG_TOMORROW = "tmrw"


@dataclass(frozen=True)
class Callback:
    """A parsed inline-button payload."""

    verb: str
    kind: str
    item_id: int
    arg: str | None = None


def parse_callback(data: str) -> Callback | None:
    """Parse ``verb:kind:id[:arg]`` into a :class:`Callback` (``None`` if invalid)."""
    parts = data.split(":")
    if len(parts) < 3:
        return None
    verb, kind, raw_id = parts[0], parts[1], parts[2]
    if not raw_id.isdigit():
        return None
    arg = parts[3] if len(parts) > 3 else None
    return Callback(verb=verb, kind=kind, item_id=int(raw_id), arg=arg)


def _cb(verb: str, kind: str, item_id: int, arg: str | None = None) -> str:
    """Encode a callback payload string."""
    base = f"{verb}:{kind}:{item_id}"
    return f"{base}:{arg}" if arg else base


# ── builders ──────────────────────────────────────────────────────────────────
def item_actions(kind: str, item_id: int, *, recurring: bool = False) -> InlineKeyboardMarkup:
    """Done / snooze buttons for a fired reminder, promise or tracked task.

    A recurring reminder shows «🚫 To'xtatish» (cancel the whole series) instead of
    snooze, since snoozing a single occurrence of a repeating reminder is moot.
    """
    if recurring:
        done = _Btn("✅ Bajarildi", callback_data=_cb(VERB_ACK, kind, item_id))
        stop = _Btn("🚫 To'xtatish", callback_data=_cb(VERB_STOP, kind, item_id))
        return InlineKeyboardMarkup([[done, stop]])
    done = _Btn("✅ Bajarildi", callback_data=_cb(VERB_DONE, kind, item_id))
    snz_1h = _Btn("⏰ 1 soatga", callback_data=_cb(VERB_SNOOZE, kind, item_id, "60"))
    snz_tm = _Btn("⏰ Ertaga", callback_data=_cb(VERB_SNOOZE, kind, item_id, ARG_TOMORROW))
    return InlineKeyboardMarkup([[done], [snz_1h, snz_tm]])


def meeting_actions(meeting_id: int) -> InlineKeyboardMarkup:
    """Acknowledge / quick-move buttons for a meeting alert."""
    ack = _Btn("✅ Tushunarli", callback_data=_cb(VERB_ACK, KIND_MEETING, meeting_id))
    mv_1h = _Btn("📅 1 soatga", callback_data=_cb(VERB_MOVE, KIND_MEETING, meeting_id, "60"))
    mv_tm = _Btn("📅 Ertaga", callback_data=_cb(VERB_MOVE, KIND_MEETING, meeting_id, ARG_TOMORROW))
    return InlineKeyboardMarkup([[ack], [mv_1h, mv_tm]])


def undo_button(kind: str, item_id: int) -> InlineKeyboardMarkup:
    """A single «❌ Bekor qilish» button for a just-created item (instant undo)."""
    cancel = _Btn("❌ Bekor qilish", callback_data=_cb(VERB_CANCEL, kind, item_id))
    return InlineKeyboardMarkup([[cancel]])


def morning_confirm_button() -> InlineKeyboardMarkup:
    """Single «✅ Tasdiqlash» button that clears the morning-plan gate."""
    confirm = _Btn("✅ Tasdiqlash", callback_data=_cb(VERB_CONFIRM, KIND_BRIEFING, 0))
    return InlineKeyboardMarkup([[confirm]])


# ── personal data (passport / car inspection / insurance) ─────────────────────
# These use a simple ``pd:<action>`` payload handled directly in on_callback
# (not the verb:kind:id codec), since they start a guided date-entry flow.
def personal_data_menu() -> InlineKeyboardMarkup:
    """Inline menu for entering tracked personal-data dates."""
    return InlineKeyboardMarkup(
        [
            [_Btn("🪪 Pasport", callback_data="pd:passport")],
            [_Btn("🚗 Texnik ko'rik", callback_data="pd:inspection")],
            [_Btn("🛡 Sug'urta", callback_data="pd:insurance")],
            [_Btn("📸 Hujjat rasmlarim", callback_data="pd:photos")],
            [_Btn("📋 Saqlangan sanalar", callback_data="pd:list")],
        ]
    )


# ── outbound channel choice (voice vs text) before a send ─────────────────────
# Simple ``out:<mode>`` payload handled directly in on_callback, like ``pd:``.
def outbound_choice_keyboard() -> InlineKeyboardMarkup:
    """Ask the owner whether to deliver the pending message by voice or text."""
    return InlineKeyboardMarkup(
        [
            [
                _Btn("🎙 Ovozli", callback_data="out:voice"),
                _Btn("📝 Matn", callback_data="out:text"),
            ]
        ]
    )


# ── document photos (passport / inspection / insurance) ───────────────────────
# Simple ``doc:<action>[:kind]`` payloads handled directly in on_callback.
def doc_review_keyboard() -> InlineKeyboardMarkup:
    """Save / cancel a freshly uploaded document photo before it is stored."""
    return InlineKeyboardMarkup(
        [
            [
                _Btn("✅ Saqlash", callback_data="doc:save"),
                _Btn("❌ Bekor qilish", callback_data="doc:cancel"),
            ]
        ]
    )


def doc_manage_keyboard(kind: str) -> InlineKeyboardMarkup:
    """Replace / delete an already-saved document of one kind."""
    return InlineKeyboardMarkup(
        [
            [
                _Btn("🔄 Yangisini yuklash", callback_data=f"doc:new:{kind}"),
                _Btn("🗑 O'chirish", callback_data=f"doc:del:{kind}"),
            ]
        ]
    )


# ── "Menga eslat" submenu (new reminder / view reminders) ─────────────────────
# Uses a simple ``mr:<action>`` payload handled directly in on_callback.
def remind_menu() -> InlineKeyboardMarkup:
    """Inline menu: add a new reminder, or view existing reminders."""
    return InlineKeyboardMarkup(
        [
            [_Btn("➕ Yangi eslatma", callback_data="mr:new")],
            [_Btn("📋 Eslatmalarim", callback_data="mr:list")],
        ]
    )


# ── end-of-day review ("Kun yakuni") ──────────────────────────────────────────
# Interactive checklist: tap the items you completed today, then «Tugatdim».
# Payloads: ``eod:done:<kind>:<id>``, ``eod:finish``, ``eod:tmrw``, ``eod:del``.
def eod_checklist(items: list[tuple[str, int, str]]) -> InlineKeyboardMarkup:
    """One tap-to-complete button per pending item + a «✔️ Tugatdim» button."""
    rows = []
    for kind, item_id, title in items[:20]:
        label = "⬜ " + (title if len(title) <= 38 else title[:37] + "…")
        rows.append([_Btn(label, callback_data=f"eod:done:{kind}:{item_id}")])
    rows.append([_Btn("✔️ Tugatdim", callback_data="eod:finish")])
    return InlineKeyboardMarkup(rows)


def eod_leftover_buttons() -> InlineKeyboardMarkup:
    """«Ertaga eslataymi?» — move leftovers to tomorrow, or delete them."""
    return InlineKeyboardMarkup(
        [
            [
                _Btn("✅ Ha, ertaga", callback_data="eod:tmrw"),
                _Btn("🗑 Yo'q, o'chir", callback_data="eod:del"),
            ]
        ]
    )
