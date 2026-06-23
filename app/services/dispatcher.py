"""Dispatcher — turn a validated :class:`RoutedIntent` into a domain action.

This is Layer 3's seam between the NLU brain and the domain services. It takes a
:class:`app.brain.intent_router.RoutedIntent` (already validated against a
pydantic model) plus a concrete ``now``, resolves names/times, calls the right
service, and returns a natural-Uzbek confirmation as a :class:`DispatchResult`.

Design notes:
  * The owner is looked up via ``person_repo.get_owner`` (never hardcoded).
  * Time phrases are parsed with :func:`app.brain.time_parse.parse_uz_time`;
    :class:`AmbiguousTime` is caught and turned into a polite Uzbek question
    instead of crashing.
  * Contact resolution may yield a :class:`Disambiguation` or ``None``; both are
    handled (a missing person is created as a lightweight contact where the flow
    needs one, e.g. delegated tasks / finance).
  * All user-facing strings are Uzbek (latin); logs/comments are English.
"""

from __future__ import annotations

import contextvars
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.bot.keyboards import (
    KIND_DECISION,
    KIND_EVENT,
    KIND_FINANCE,
    KIND_MEETING,
    KIND_MESSAGE,
    KIND_PROMISE,
    KIND_REMINDER,
    KIND_TASK,
    contact_pick_keyboard,
    debt_settle_keyboard,
    outbound_choice_keyboard,
    time_day_keyboard,
    undo_button,
)
from app.brain.contacts import ContactMatch, Disambiguation, resolve_contact
from app.brain.time_parse import AmbiguousTime, parse_uz_time
from app.brain.translit import normalize_name
from app.db.models.enums import (
    DebtDirection,
    EventCategory,
    NotifyTargetKind,
    SendMode,
    Source,
    TaskKind,
)
from app.integrations.google.calendar import add_calendar_event
from app.logging_conf import get_logger
from app.repositories import (
    finance_repo,
    meeting_repo,
    person_repo,
    reminder_repo,
    task_repo,
)
from app.services._timeutil import as_utc, to_local_str

if TYPE_CHECKING:
    from app.brain.intent_router import RoutedIntent
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


@dataclass
class DispatchResult:
    """The reply the bot should send back to the owner.

    ``parse_mode`` ("HTML"/"Markdown"/None) is passed straight to Telegram so
    rich list replies (digest, debts, agenda, contacts) can use bold, block
    quotes and monospace tables; plain confirmations leave it ``None``.
    """

    text: str
    reply_markup: Any | None = None
    parse_mode: str | None = None


# ── pending contact disambiguation (numbered "which one?" selection) ───────────
@dataclass
class _PendingChoice:
    """A paused action awaiting the owner's numbered pick among same-name contacts."""

    intent_name: str
    params: Any
    field: str
    candidate_ids: list[int]
    candidate_labels: list[str]


# Single owner, so a small in-memory store keyed by the owner chat id is enough.
# It is intentionally non-persistent: a disambiguation is a short, live exchange.
_PENDING: dict[int, _PendingChoice] = {}

# During a selection re-run this carries the chosen person id so contact
# resolution returns exactly that person instead of re-prompting.
_forced_person_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "forced_person_id", default=None
)


def has_pending(owner_key: int) -> bool:
    """True when the owner has an unanswered disambiguation prompt."""
    return owner_key in _PENDING


def clear_pending(owner_key: int) -> None:
    """Drop any pending disambiguation for the owner (e.g. they moved on)."""
    _PENDING.pop(owner_key, None)


# ── pending outbound: a message awaiting the owner's voice/text channel pick ───
@dataclass
class _PendingOutbound:
    """A resolved message paused until the owner taps «🎙 Ovozli» or «📝 Matn».

    ``kind`` is ``"send"`` (deliver now) or ``"schedule"`` (deliver at
    ``send_at``). The recipient is already resolved, so the tap only chooses the
    channel and fires the existing send/schedule path.
    """

    kind: str
    recipient_id: int
    display_name: str
    content: str
    is_owner: bool
    send_at: datetime | None = None
    # Meeting-notice messages are delivered NOW and again at ``send_at`` (a
    # heads-up plus a reminder at the meeting time). Set for kind=="schedule".
    also_send_now: bool = False


_PENDING_OUT: dict[int, _PendingOutbound] = {}


def has_pending_outbound(owner_key: int) -> bool:
    """True when a message is waiting for the owner's voice/text choice."""
    return owner_key in _PENDING_OUT


def clear_pending_outbound(owner_key: int) -> None:
    """Drop any message awaiting a voice/text choice (e.g. the owner moved on)."""
    _PENDING_OUT.pop(owner_key, None)


# ── pending compose: a contact picked from a "show contacts" list, awaiting the
#    message body the owner will type/voice next ───────────────────────────────
@dataclass
class _PendingCompose:
    """A contact chosen from a contact-search list; next message becomes its body."""

    recipient_id: int
    display_name: str


_PENDING_COMPOSE: dict[int, _PendingCompose] = {}


def has_pending_compose(owner_key: int) -> bool:
    """True when a picked contact is awaiting the message the owner will send it."""
    return owner_key in _PENDING_COMPOSE


def clear_pending_compose(owner_key: int) -> None:
    """Drop a pending compose (the owner moved on without writing the message)."""
    _PENDING_COMPOSE.pop(owner_key, None)


def _begin_compose(
    owner_key: int, person_id: int, display_name: str
) -> DispatchResult:
    """Arm a compose: the owner's next message is sent to ``display_name``."""
    _PENDING_COMPOSE[owner_key] = _PendingCompose(
        recipient_id=person_id, display_name=display_name
    )
    return DispatchResult(
        f"✍️ <b>{html.escape(display_name, quote=False)}</b>ga xabaringizni "
        "yozing yoki ovozli yuboring.",
        parse_mode="HTML",
    )


async def dispatch_compose(
    registry: ServiceRegistry, content: str, *, now: datetime
) -> DispatchResult | None:
    """Send ``content`` to the contact picked earlier from a contact-search list.

    Reuses the normal send path (forcing the already-chosen person so it never
    re-disambiguates), which then asks «🎙 Ovozli | 📝 Matn» before delivering.
    Returns ``None`` when there is no pending compose.
    """
    from app.brain.intent_router import RoutedIntent
    from app.brain.intents import SendMessage

    owner_key = registry.settings.owner_chat_id
    pending = _PENDING_COMPOSE.pop(owner_key, None)
    if pending is None:
        return None
    if not (content or "").strip():
        return DispatchResult("Xabar matni bo'sh. Nima yuborishni ayting.")

    token = _forced_person_id.set(pending.recipient_id)
    try:
        routed = RoutedIntent(
            "send_message",
            SendMessage(recipient_name=pending.display_name, content=content),
            {},
        )
        return await dispatch(registry, routed, now=now)
    finally:
        _forced_person_id.reset(token)


async def settle_debt(
    registry: ServiceRegistry, record_id: int, dir_code: str, *, now: datetime
) -> tuple[str, DispatchResult]:
    """Mark a debt settled (paid) and return ``(toast, refreshed debts list)``.

    Settling is a SOFT close (status=settled, reminder job cancelled) — the record
    stays for history but drops out of the open-debts list, which is re-rendered in
    the same view the owner was looking at.
    """
    from app.brain.intent_router import RoutedIntent
    from app.brain.intents import ListFinance

    settled = await registry.finance_service.settle(record_id)
    name = "Qarz"
    if settled is not None:
        async with registry.session() as session:
            counterparty = await person_repo.get_by_id(
                session, settled.counterparty_id
            )
        name = getattr(counterparty, "display_name", None) or "Qarz"
    toast = (
        f"✅ {name} qarzi yopildi"
        if settled is not None
        else "Bu yozuv topilmadi yoki allaqachon yopilgan"
    )
    direction = {"t": "they_owe_me", "i": "i_owe_them", "a": "all"}.get(
        dir_code, "all"
    )
    relist = await dispatch(
        registry,
        RoutedIntent("list_finance", ListFinance(direction=direction), {}),
        now=now,
    )
    return toast, relist


# ── pending time: a scheduling intent whose day/clock the owner must pin down ──
# Intents that REQUIRE a precise day+clock; maps each to its TimeSpec field. An
# AmbiguousTime from these triggers the interactive day/time clarification.
_TIME_FIELD = {
    "create_reminder": "when",
    "create_promise": "deadline",
    "assign_task_with_followup": "deadline",
    "schedule_meeting": "when",
    "schedule_message": "when",
}


@dataclass
class _PendingTime:
    """A scheduling action paused until its day and clock are both pinned down."""

    intent_name: str
    params: Any
    field: str
    day: date | None = None
    clock: tuple[int, int] | None = None
    awaiting: str = "day"  # "day" | "clock" | "date_text"


_PENDING_TIME: dict[int, _PendingTime] = {}


def has_pending_time(owner_key: int) -> bool:
    """True when a scheduling action is awaiting a typed time/date answer."""
    pending = _PENDING_TIME.get(owner_key)
    return pending is not None and pending.awaiting in ("clock", "date_text")


def clear_pending_time(owner_key: int) -> None:
    """Drop a pending time clarification (the owner moved on)."""
    _PENDING_TIME.pop(owner_key, None)


_DAY_PROMPT = "📅 Qaysi kunga belgilab qo'yay? Kunni tanlang:"
_CLOCK_PROMPT = "🕒 Soat nechada? Vaqtni yozing — masalan «22:00» yoki «9:30»."
_DATE_PROMPT = "📅 Sanani yozing — masalan «25.06.2026»."


def _begin_time_clarify(
    registry: ServiceRegistry, name: str, params: Any, field: str, exc: AmbiguousTime
) -> DispatchResult:
    """Pause a scheduling action and ask for the missing day and/or clock."""
    owner_key = registry.settings.owner_chat_id
    pending = _PendingTime(
        intent_name=name,
        params=params,
        field=field,
        day=getattr(exc, "day_date", None),
        clock=getattr(exc, "clock", None),
    )
    if pending.day is None:
        pending.awaiting = "day"
        _PENDING_TIME[owner_key] = pending
        return DispatchResult(_DAY_PROMPT, reply_markup=time_day_keyboard())
    # Day already known (only the clock is missing) -> ask for the clock.
    pending.awaiting = "clock"
    _PENDING_TIME[owner_key] = pending
    return DispatchResult(_CLOCK_PROMPT)


def _day_from_code(code: str, today: date) -> date | None:
    """Resolve a ``tday:<code>`` to a concrete date (``None`` for 'other')."""
    if code.startswith("d") and code[1:].isdigit():
        return today + timedelta(days=int(code[1:]))
    if code.startswith("w") and code[1:].isdigit():
        weekday = int(code[1:]) % 7  # 0=Mon..6=Sun
        ahead = (weekday - today.weekday()) % 7  # today if it matches
        return today + timedelta(days=ahead)
    return None


async def resume_time_day(
    registry: ServiceRegistry, code: str, *, now: datetime
) -> DispatchResult | None:
    """Apply a tapped day button, then finalize or ask for the clock/date."""
    owner_key = registry.settings.owner_chat_id
    pending = _PENDING_TIME.get(owner_key)
    if pending is None:
        return None
    if code == "other":
        pending.awaiting = "date_text"
        return DispatchResult(_DATE_PROMPT)
    today = now.astimezone(ZoneInfo(registry.settings.user_timezone)).date()
    chosen = _day_from_code(code, today)
    if chosen is None:
        return DispatchResult(_DATE_PROMPT)
    pending.day = chosen
    if pending.clock is None:
        pending.awaiting = "clock"
        return DispatchResult(_CLOCK_PROMPT)
    return await _finalize_time(registry, pending, now=now)


_CLOCK_TEXT_RE = re.compile(r"^\s*(?:soat\s*)?(\d{1,2})(?::(\d{2}))?\s*$", re.IGNORECASE)


def _parse_clock_text(text: str) -> tuple[int, int] | None:
    """Parse a bare typed time ('22:00', '9:30', 'soat 9') to ``(hour, minute)``."""
    match = _CLOCK_TEXT_RE.match(text or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


async def resume_time_text(
    registry: ServiceRegistry, text: str, *, now: datetime
) -> DispatchResult | None:
    """Apply a typed clock (or date) answer to the pending scheduling action."""
    from app.brain.time_parse import parse_date

    owner_key = registry.settings.owner_chat_id
    pending = _PENDING_TIME.get(owner_key)
    if pending is None:
        return None

    if pending.awaiting == "date_text":
        parsed = parse_date(text)
        if parsed is None:
            return DispatchResult(
                "📅 Sanani tushunolmadim. KK.OO.YYYY ko'rinishida yozing "
                "(masalan 25.06.2026)."
            )
        pending.day = parsed
        if pending.clock is None:
            pending.awaiting = "clock"
            return DispatchResult(_CLOCK_PROMPT)
        return await _finalize_time(registry, pending, now=now)

    # awaiting == "clock"
    clock = _parse_clock_text(text)
    if clock is None:
        return DispatchResult(
            "🕒 Vaqtni tushunolmadim. Masalan «22:00» yoki «9:30» deb yozing."
        )
    pending.clock = clock
    if pending.day is None:  # defensive: day should already be set
        pending.awaiting = "day"
        return DispatchResult(_DAY_PROMPT, reply_markup=time_day_keyboard())
    return await _finalize_time(registry, pending, now=now)


async def _finalize_time(
    registry: ServiceRegistry, pending: _PendingTime, *, now: datetime
) -> DispatchResult:
    """Stamp the resolved day+clock onto the intent and run it."""
    from app.brain.intent_router import RoutedIntent
    from app.brain.intents import TimeSpec

    owner_key = registry.settings.owner_chat_id
    clear_pending_time(owner_key)
    hour, minute = pending.clock or (9, 0)
    resolved = datetime.combine(pending.day, time(hour, minute))
    raw = resolved.strftime("%Y-%m-%d %H:%M")
    setattr(pending.params, pending.field, TimeSpec(raw=raw, kind="absolute"))
    routed = RoutedIntent(pending.intent_name, pending.params, {})
    logger.info(
        "dispatch.resume_time", intent=pending.intent_name, resolved=raw
    )
    return await dispatch(registry, routed, now=now)


def _outbound_prompt(display_name: str, content: str) -> DispatchResult:
    """Ask how to deliver the pending message: a preview + voice/text buttons."""
    preview = content if len(content) <= 200 else content[:199] + "…"
    return DispatchResult(
        f"📨 <b>{html.escape(display_name, quote=False)}</b>ga xabar tayyor:\n"
        f"«{html.escape(preview, quote=False)}»\n\n"
        "Qanday yuboray?",
        reply_markup=outbound_choice_keyboard(),
        parse_mode="HTML",
    )


async def complete_outbound(
    registry: ServiceRegistry, owner_key: int, mode: SendMode
) -> DispatchResult:
    """Deliver (or schedule) the pending message via the chosen channel.

    Called by the ``out:<mode>`` button handler. Returns the same confirmation
    the immediate send/schedule used to return, now that the channel is known.
    """
    pending = _PENDING_OUT.pop(owner_key, None)
    if pending is None:
        return DispatchResult(
            "Bu so'rov eskirgan. Iltimos, xabarni qaytadan ayting."
        )

    if pending.kind == "schedule" and pending.send_at is not None:
        # Meeting notice: deliver immediately too, so the contact gets a heads-up
        # now and the very same message again at the meeting time.
        if pending.also_send_now:
            await registry.message_service.send_message_now(
                recipient_id=pending.recipient_id,
                content=pending.content,
                delivery=mode,
                source=Source.nlu,
            )
        message = await registry.message_service.schedule_message(
            recipient_id=pending.recipient_id,
            content=pending.content,
            delivery=mode,
            send_at=pending.send_at,
            source=Source.nlu,
        )
        if pending.also_send_now:
            text = (
                f"✅ {pending.display_name}ga xabar hozir yuborildi.\n"
                f"🔁 🕒 {_local(pending.send_at, registry)} da yana yuboriladi."
            )
        else:
            text = (
                f"✉️ {pending.display_name}ga xabar rejalashtirildi.\n"
                f"🕒 Vaqt: {_local(pending.send_at, registry)}"
            )
        text += _delivery_note(registry, mode)
        text += _test_mode_note(registry, pending.is_owner)
        return DispatchResult(
            text, reply_markup=undo_button(KIND_MESSAGE, message.id)
        )

    await registry.message_service.send_message_now(
        recipient_id=pending.recipient_id,
        content=pending.content,
        delivery=mode,
        source=Source.nlu,
    )
    text = f"{pending.display_name}ga xabar yuborildi."
    text += _delivery_note(registry, mode)
    text += _test_mode_note(registry, pending.is_owner)
    return DispatchResult(text)


def _script_hint(text: str) -> str:
    """Label a name's alphabet so Latin/Cyrillic twins are distinguishable."""
    return "kiril" if any("Ѐ" <= ch <= "ӿ" for ch in text) else "lotin"


def _match_from_person(person: Any) -> ContactMatch:
    """Build a :class:`ContactMatch` from a resolved ``Person`` row."""
    return ContactMatch(
        person_id=person.id,
        chat_id=person.telegram_user_id,
        display_name=person.display_name,
        honorific=person.honorific,
        default_send_mode=person.default_send_mode,
        confidence=1.0,
        phone=person.phone,
        username=person.telegram_username,
    )


# A send-disambiguation (the owner typed a specific recipient) rarely has many
# namesakes, so a handful is enough. A "show me X's contacts" lookup is different:
# the owner wants to SEE every namesake/variant and pick, so it shows many more.
_MAX_PICK = 8
_MAX_CONTACT_LIST = 50


def _looks_like_phone(text: str) -> bool:
    """True when the recipient string is a phone number rather than a name."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    # Only phone characters (digits, spaces, +, -, parentheses) and enough digits
    # that it cannot be a name with a trailing year ("Akmal 2021").
    if re.fullmatch(r"[+()\d\s-]+", stripped) is None:
        return False
    return len(re.sub(r"\D", "", stripped)) >= 7


def _candidate_detail(candidate: ContactMatch) -> str:
    """A short distinguishing detail so identical names are tellable apart."""
    if candidate.phone:
        return f"📞 {candidate.phone}"
    if candidate.username:
        return f"@{candidate.username}"
    return _script_hint(candidate.display_name)


def _order_candidates(
    candidates: list[ContactMatch], *, limit: int = _MAX_PICK
) -> list[ContactMatch]:
    """Reachable contacts (have a Telegram id) first, then cap to ``limit``.

    The sort is stable, so any similarity ranking from ``search_by_name`` is
    preserved within the reachable/unreachable groups.
    """
    return sorted(candidates, key=lambda c: c.chat_id is None)[:limit]


def _numbered_prompt(name: str, candidates: list[ContactMatch]) -> str:
    """Build a numbered 'which one?' prompt with a distinguishing detail each."""
    lines = [
        f"«{name}» bo'yicha {len(candidates)} ta kontakt topildi. "
        "Qaysi biri? Raqamini tanlang yoki yozing:"
    ]
    for i, candidate in enumerate(candidates, start=1):
        line = f"{i}. {candidate.display_name} — {_candidate_detail(candidate)}"
        if candidate.chat_id is None:
            line += " ⚠️ Telegram ID yo'q"
        lines.append(line)
    return "\n".join(lines)


# ── short-term contact memory (coreference) ───────────────────────────────────
# After the owner names a contact (send/schedule/meeting/assign), a follow-up
# command may refer to that same person only by pronoun ("unga ...", "o'sha
# odamga ...") or leave the name out entirely. We remember the last contact the
# owner acted on, per owner, so the follow-up resolves without re-naming. It is
# in-memory only and stays until a NEW contact name is used (which overwrites
# it) — a conversational convenience, not a persisted record.
@dataclass
class _LastContact:
    """The contact the owner most recently acted on (for vague follow-ups)."""

    person_id: int
    display_name: str


_LAST_CONTACT: dict[int, _LastContact] = {}


def clear_last_contact(owner_key: int) -> None:
    """Forget the remembered contact (used by tests / explicit resets)."""
    _LAST_CONTACT.pop(owner_key, None)


def _remember_contact(owner_key: int, match: ContactMatch) -> None:
    """Record ``match`` as the contact a vague follow-up should resolve to."""
    _LAST_CONTACT[owner_key] = _LastContact(
        person_id=match.person_id, display_name=match.display_name
    )


def _peek_last_contact(owner_key: int) -> _LastContact | None:
    """The remembered contact, or ``None`` if the owner hasn't named one yet."""
    return _LAST_CONTACT.get(owner_key)


# Pronoun / demonstrative forms that, used alone, point back at the last contact
# ("unga xabar yubor", "o'shanga ayt"). Normalized so apostrophes/script differ
# harmlessly ("o'sha" == "osha").
_REF_PRONOUNS = frozenset(
    normalize_name(w)
    for w in (
        "u", "uni", "unga", "undan", "ul", "shu", "shuni", "shunga", "shundan",
        "o'sha", "o'shani", "o'shanga", "o'shandan", "o'shu", "ushbu",
        "usha", "ushani", "ushanga", "o'zi", "o'ziga", "o'zini", "vu",
    )
)
# Demonstratives that may LEAD a "<dem> <person-noun>" phrase ("o'sha odam").
_REF_DEMONSTRATIVES = frozenset(
    normalize_name(w)
    for w in ("u", "shu", "o'sha", "o'shu", "ushbu", "usha", "mana", "ana")
)
# Generic person nouns (incl. kinship honorifics) that refer back AFTER a
# demonstrative ("o'sha odamga", "shu opaga", "o'sha bolaga").
_REF_PERSON_NOUNS = frozenset(
    normalize_name(w)
    for w in (
        "odam", "kishi", "inson", "kontakt", "bola", "bolakay", "yigit", "qiz",
        "ayol", "erkak", "aka", "opa", "uka", "amaki", "xola", "toga", "ona",
        "ota", "buvi", "bobo", "singil", "kelin", "jiyan", "og'a", "apa",
    )
)
# Person nouns unambiguous enough to stand ALONE (no contact is saved as these).
_REF_SOLO_NOUNS = frozenset(normalize_name(w) for w in ("kontakt", "kishi", "inson"))

# Trailing dative/locative case suffixes peeled before matching a person noun
# ("odamga" -> "odam", "kishiga" -> "kishi"). Pronouns are matched whole first,
# so their glued "-ga" ("unga", "shunga") is never stripped.
_REF_CASE_SUFFIXES = ("niki", "ning", "ga", "ka", "qa", "ni", "dan", "da", "cha")


def _ref_stem(token: str) -> str:
    """Peel one trailing case suffix off a normalized token (>=3-char stem kept)."""
    for suf in _REF_CASE_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: -len(suf)]
    return token


def _refers_to_last_contact(name: str) -> bool:
    """True when ``name`` is a bare pronoun or "<dem> <person>" (no real name).

    Matches "u", "unga", "o'sha odam", "shu kishiga", "o'sha opaga". A generic
    word with NO demonstrative ("Odamga", "Qizga") does NOT match, so a contact
    that happens to be such a word is never hijacked.
    """
    toks = [normalize_name(t) for t in (name or "").split()]
    toks = [t for t in toks if t]
    if not toks:
        return False
    has_dem = any(t in _REF_DEMONSTRATIVES for t in toks)
    for t in toks:
        if t in _REF_PRONOUNS or t in _REF_DEMONSTRATIVES:
            continue
        stem = _ref_stem(t)
        if stem in _REF_SOLO_NOUNS:
            continue
        if has_dem and (stem in _REF_PERSON_NOUNS or t in _REF_PERSON_NOUNS):
            continue
        return False
    return True


def _is_contact_reference(name: str) -> bool:
    """True when ``name`` is empty or refers back to the last-mentioned contact."""
    if not (name or "").strip():
        return True
    return _refers_to_last_contact(name)


async def _resolve_or_pend(
    registry: ServiceRegistry,
    name: str,
    *,
    intent_name: str,
    params: Any,
    field: str,
    required: bool = True,
) -> ContactMatch | DispatchResult | None:
    """Resolve a contact, or pause for a numbered pick when several match.

    Returns a :class:`ContactMatch` to continue; a :class:`DispatchResult` (a
    numbered prompt, stored as pending, or a not-found message) the caller should
    return; or ``None`` when not found and ``required`` is ``False`` (the caller
    proceeds without a contact). A forced id (set during a selection re-run)
    short-circuits resolution to the chosen person. A vague follow-up (a pronoun
    or no name) resolves to the last contact the owner acted on; every successful
    resolution is remembered so the NEXT such follow-up has something to point at.
    """
    owner_key = registry.settings.owner_chat_id
    forced = _forced_person_id.get()
    if forced is not None:
        async with registry.session() as session:
            person = await person_repo.get_by_id(session, forced)
        if person is not None:
            match = _match_from_person(person)
            _remember_contact(owner_key, match)
            return match

    # Coreference: a bare pronoun ("unga"), a "<dem> <person>" phrase ("o'sha
    # odamga"), or no name at all reuses the contact the owner just acted on.
    if _is_contact_reference(name):
        last = _peek_last_contact(owner_key)
        if last is not None:
            async with registry.session() as session:
                person = await person_repo.get_by_id(session, last.person_id)
            if person is not None:
                match = _match_from_person(person)
                _remember_contact(owner_key, match)
                # Replace the pronoun with the real name so downstream
                # confirmations ("... ga yuborildi") read naturally.
                setattr(params, field, match.display_name)
                return match
        # Referred back to "them" but no contact has been named yet this session.
        return DispatchResult(
            "Kimga ekanini aniqlay olmadim — iltimos, kontakt nomini ayting."
        )

    resolved = await _resolve_recipient(registry, name)
    if isinstance(resolved, ContactMatch):
        _remember_contact(owner_key, resolved)
        return resolved
    if resolved is None:
        logger.info("dispatch.contact.not_found", intent=intent_name, name=name)
        if not required:
            return None
        return DispatchResult(
            f"\"{name}\" kontaktlarda topilmadi. "
            "Telefoningizdagi kontakt nomini aniqroq ayting."
        )

    # Several matched (e.g. two "Akmal"s, or a Latin + a Cyrillic spelling):
    # remember the action and ask the owner to pick — by tapping a numbered
    # button or typing the number. The phone/@username on each line tells
    # identical names apart. The choice resumes the original action.
    candidates = _order_candidates(resolved.candidates)
    _PENDING[registry.settings.owner_chat_id] = _PendingChoice(
        intent_name=intent_name,
        params=params,
        field=field,
        candidate_ids=[c.person_id for c in candidates],
        candidate_labels=[c.display_name for c in candidates],
    )
    return DispatchResult(
        _numbered_prompt(name, candidates),
        reply_markup=contact_pick_keyboard([c.person_id for c in candidates]),
    )


async def resume_choice(
    registry: ServiceRegistry, selection: int, *, now: datetime
) -> DispatchResult | None:
    """Complete a pending disambiguation with the chosen 1-based number.

    Returns ``None`` when there is nothing pending (the caller then treats the
    message as a normal command).
    """
    from app.brain.intent_router import RoutedIntent

    owner_key = registry.settings.owner_chat_id
    pending = _PENDING.get(owner_key)
    if pending is None:
        return None
    if not 1 <= selection <= len(pending.candidate_ids):
        return DispatchResult(
            f"Iltimos, 1 dan {len(pending.candidate_ids)} gacha raqam yozing."
        )
    chosen_id = pending.candidate_ids[selection - 1]
    chosen_label = pending.candidate_labels[selection - 1]
    clear_pending(owner_key)

    # A contact chosen from a "show contacts" list -> start composing a message.
    if pending.intent_name == "compose_pick":
        return _begin_compose(owner_key, chosen_id, chosen_label)

    token = _forced_person_id.set(chosen_id)
    try:
        routed = RoutedIntent(pending.intent_name, pending.params, {})
        logger.info(
            "dispatch.resume_choice", intent=pending.intent_name, chosen=chosen_label
        )
        return await dispatch(registry, routed, now=now)
    finally:
        _forced_person_id.reset(token)


async def resume_choice_pid(
    registry: ServiceRegistry, person_id: int, *, now: datetime
) -> DispatchResult | None:
    """Complete a pending disambiguation from a tapped «pick» button.

    The button payload carries the concrete ``person_id`` (not an index), so the
    pick is unambiguous. Returns ``None`` when nothing is pending, or a stale
    note when the id is no longer among the offered candidates.
    """
    from app.brain.intent_router import RoutedIntent

    owner_key = registry.settings.owner_chat_id
    pending = _PENDING.get(owner_key)
    if pending is None:
        return None
    if person_id not in pending.candidate_ids:
        return DispatchResult(
            "Bu tanlov eskirgan. Iltimos, buyruqni qaytadan ayting."
        )
    chosen_label = pending.candidate_labels[pending.candidate_ids.index(person_id)]
    clear_pending(owner_key)

    # A contact chosen from a "show contacts" list -> start composing a message.
    if pending.intent_name == "compose_pick":
        return _begin_compose(owner_key, person_id, chosen_label)

    token = _forced_person_id.set(person_id)
    try:
        routed = RoutedIntent(pending.intent_name, pending.params, {})
        logger.info(
            "dispatch.resume_choice_pid",
            intent=pending.intent_name,
            chosen=chosen_label,
        )
        return await dispatch(registry, routed, now=now)
    finally:
        _forced_person_id.reset(token)


# Contact-bearing fields on the intents whose recipient a follow-up may correct.
_CONTACT_FIELDS = ("recipient_name", "assignee_name", "notify_target_name")


def _recipient_of(routed: RoutedIntent) -> str | None:
    """The contact name a routed intent points at, if it is a messaging-type one."""
    params = routed.params
    if params is None:
        return None
    for field in _CONTACT_FIELDS:
        value = getattr(params, field, None)
        if value:
            return str(value)
    return None


async def resume_with_correction(
    registry: ServiceRegistry,
    routed: RoutedIntent,
    *,
    raw_text: str,
    now: datetime,
) -> DispatchResult:
    """Handle a non-number reply that arrives while a contact pick is pending.

    The owner either (a) re-specifies the contact for the paused action — by
    typing just a name ("Doniyor aka og'am ga") or "…ga yubor" — in which case
    we swap that name into the SAME intent and resume it (keeping the original
    message/time); or (b) issues a brand-new, non-contact command, which simply
    supersedes the pick. Distinguished by whether the freshly-routed intent
    names a recipient.
    """
    from app.brain.intent_router import RoutedIntent

    owner_key = registry.settings.owner_chat_id
    pending = _PENDING.get(owner_key)
    new_name = _recipient_of(routed)

    # A "show contacts" pick list (compose_pick) carries no resumable params, so a
    # non-number reply here just means a new search/command — route it fresh.
    if pending is not None and pending.params is None:
        clear_pending(owner_key)
        return await dispatch(registry, routed, now=now)

    # A genuinely different command (no recipient, e.g. add_finance, a reminder)
    # supersedes the pending pick.
    if pending is None or (routed.name != "unknown" and new_name is None):
        clear_pending(owner_key)
        return await dispatch(registry, routed, now=now)

    # Otherwise treat the reply as the corrected contact for the paused action:
    # a routed recipient if the model found one, else the raw text (a bare name).
    name = new_name or raw_text.strip()
    clear_pending(owner_key)
    setattr(pending.params, pending.field, name)
    logger.info(
        "dispatch.resume_with_correction", intent=pending.intent_name, name=name
    )
    corrected = RoutedIntent(pending.intent_name, pending.params, {})
    return await dispatch(registry, corrected, now=now)


# ── small helpers ────────────────────────────────────────────────────────────
def _explicit_send_mode(delivery: object) -> SendMode | None:
    """The owner's explicitly-stated channel, or ``None`` when unspecified.

    The brain sets ``delivery="ask"`` when the owner did not say how to send the
    message — that returns ``None`` so the caller shows the voice/text buttons.
    A concrete ``voice``/``text``/``both`` is honoured directly, no prompt.
    """
    value = getattr(delivery, "value", delivery)
    if value in ("voice", "text", "both"):
        try:
            return SendMode(value)
        except ValueError:
            return None
    return None


def _local(dt: datetime, registry: ServiceRegistry) -> str:
    """Format a UTC datetime in the owner's local zone for confirmations."""
    return to_local_str(dt, registry.settings.user_timezone)


async def _resolve_recipient(
    registry: ServiceRegistry, name: str
) -> ContactMatch | Disambiguation | None:
    """Resolve a recipient by saved-contact name, syncing on a first miss.

    The owner addresses people by the name saved in their phone; those names
    reach the DB via the startup Telegram contact sync. If the name is not found
    yet (e.g. a contact added after startup), pull the owner's address book once
    via the userbot and retry — so freshly added contacts still resolve.
    """
    async with registry.session() as session:
        resolved = await resolve_contact(session, name)
    if resolved is not None:
        return resolved
    if registry.userbot is None:
        return None

    # Not found yet: pull the owner's address book once (covers a contact added
    # after startup) and retry.
    from app.userbot.contacts import sync_contacts

    await sync_contacts(registry.userbot, registry)
    async with registry.session() as session:
        resolved = await resolve_contact(session, name)
    if resolved is not None:
        return resolved

    # Still nothing. If the owner addressed a raw phone number (not a saved
    # contact), import it as a Telegram contact so a brand-new number can be
    # messaged, then resolve to that freshly created person.
    if _looks_like_phone(name):
        return await _import_phone_recipient(registry, name)
    return None


async def _import_phone_recipient(
    registry: ServiceRegistry, phone: str
) -> ContactMatch | None:
    """Import a raw phone as a contact and return a match (``None`` if not on TG)."""
    from app.userbot.contacts import import_phone_contact

    imported = await import_phone_contact(registry.userbot, phone)
    if imported is None:
        return None
    async with registry.session() as session:
        person = await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=imported["user_id"],
            display_name=imported.get("name") or phone,
            username=imported.get("username"),
            phone=imported.get("phone") or phone,
        )
        return _match_from_person(person)


def _delivery_note(registry: ServiceRegistry, delivery: SendMode) -> str:
    """Warn the owner when a voice send will fall back to text (no clone set)."""
    if delivery in (SendMode.voice, SendMode.both):
        voice = registry.voice_service
        if voice is None or not voice.available():
            return (
                "\n(Eslatma: ovoz xizmati hozir mavjud emas, xabar matn shaklida "
                "yuboriladi.)"
            )
    return ""


def _test_mode_note(registry: ServiceRegistry, is_owner: bool) -> str:
    """Tell the owner the send was a TEST redirect (not delivered to the contact)."""
    if registry.settings.test_mode and not is_owner:
        return (
            "\n(TEST rejimi: xabar haqiqiy kontaktga emas, sizga (preview) "
            "yuborildi. Haqiqiy yuborish uchun .env da TEST_MODE=false qiling.)"
        )
    return ""


_GOOGLE_REAUTH = (
    "Google ruxsati tugagan yoki bekor qilingan. Terminalda qayta ulang:\n"
    "python -m scripts.google_auth"
)


def _is_google_auth_error(exc: Exception) -> bool:
    """True when ``exc`` looks like an expired/revoked Google OAuth token."""
    if type(exc).__name__ in ("RefreshError", "DefaultCredentialsError"):
        return True
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "invalid_grant",
            "invalid_credentials",
            "token has been expired",
            "401",
            "403",
            "insufficient permission",
        )
    )


# ── dispatch ──────────────────────────────────────────────────────────────────
async def dispatch(
    registry: ServiceRegistry, routed: RoutedIntent, *, now: datetime
) -> DispatchResult:
    """Execute ``routed`` and return an Uzbek confirmation/clarification."""
    name = routed.name
    params = routed.params

    if name == "unknown" or params is None:
        return DispatchResult("Tushunmadim, qaytaroq ayting.")

    handler = _HANDLERS.get(name)
    if handler is None:
        logger.warning("dispatch.no_handler", intent=name)
        return DispatchResult("Tushunmadim, qaytaroq ayting.")

    try:
        return await handler(registry, params, now)
    except AmbiguousTime as exc:
        # A scheduling intent with a vague time -> ask for the missing day/clock
        # via buttons/prompt and DON'T create anything until it's pinned down.
        field = _TIME_FIELD.get(name)
        if field is not None:
            return _begin_time_clarify(registry, name, params, field, exc)
        return DispatchResult(str(exc))
    except Exception:  # noqa: BLE001 - never crash the bot loop
        logger.exception("dispatch.failed", intent=name)
        return DispatchResult(
            "Xatolik yuz berdi. Iltimos, biroz o'zgartirib qaytadan urinib ko'ring."
        )


# ── individual intent handlers ────────────────────────────────────────────────
async def _create_reminder(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    # Recurring reminder ("har dushanba", "oy oxirida"): build cron fields + a
    # human label, and seed due_at with the next occurrence for display.
    cron_fields, recur_label = _recurrence_to_cron(getattr(params, "recurrence", None))
    if cron_fields is not None:
        from app.scheduler.jobs import next_cron_run

        when_dt = next_cron_run(
            cron_fields, registry.settings.user_timezone, now
        ) or as_utc(now)
        reminder = await registry.reminder_service.create_reminder(
            owner_id=owner_id,
            title=params.text,
            when_dt=when_dt,
            recurrence=recur_label,
            cron_fields=cron_fields,
            source=Source.nlu,
        )
        return DispatchResult(
            f"🔁 Takroriy eslatma qo'yildi: {params.text}\n"
            f"📆 Jadval: {recur_label}\n"
            f"🕒 Keyingi: {_local(when_dt, registry)}",
            reply_markup=undo_button(KIND_REMINDER, reminder.id),
        )

    when_dt = parse_uz_time(
        params.when, now, registry.settings.user_timezone, require_clock=True
    )
    reminder = await registry.reminder_service.create_reminder(
        owner_id=owner_id,
        title=params.text,
        when_dt=when_dt,
        pre_alerts_minutes=params.pre_alerts_minutes,
        source=Source.nlu,
    )
    # Best-effort: also place the dated reminder on the owner's Google Calendar.
    cal_link = await add_calendar_event(
        registry.calendar_service, title=params.text, start=when_dt
    )
    text = f"⏰ Eslatma qo'yildi: {params.text}\n🕒 Vaqt: {_local(when_dt, registry)}"
    if cal_link:
        text += "\n📅 Kalendarga ham qo'shildi."
    return DispatchResult(text, reply_markup=undo_button(KIND_REMINDER, reminder.id))


# Weekday index (0=Mon..6=Sun) -> APScheduler day_of_week name (avoids the
# 0=mon-vs-0=sun ambiguity) and the Uzbek label shown back to the owner.
_WEEKDAY_CRON = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_UZ = (
    "dushanba",
    "seshanba",
    "chorshanba",
    "payshanba",
    "juma",
    "shanba",
    "yakshanba",
)


def _recurrence_to_cron(recurrence: Any) -> tuple[dict | None, str]:
    """Convert a ``RecurrenceSpec`` to ``(cron_fields, uzbek_label)``.

    Returns ``(None, "")`` for a one-shot reminder (no/``none`` recurrence), so the
    caller falls back to the normal date-based scheduling path.
    """
    freq = getattr(recurrence, "freq", "none") if recurrence is not None else "none"
    if not freq or freq == "none":
        return None, ""

    hour = int(getattr(recurrence, "hour", 9) or 0)
    minute = int(getattr(recurrence, "minute", 0) or 0)
    hour = min(max(hour, 0), 23)
    minute = min(max(minute, 0), 59)
    clock = f"{hour:02d}:{minute:02d}"

    if freq == "daily":
        return {"hour": hour, "minute": minute}, f"Har kuni {clock}"

    if freq == "weekly":
        wd = getattr(recurrence, "weekday", None)
        wd = int(wd) if wd is not None else 0
        wd = min(max(wd, 0), 6)
        return (
            {"day_of_week": _WEEKDAY_CRON[wd], "hour": hour, "minute": minute},
            f"Har {_WEEKDAY_UZ[wd]} {clock}",
        )

    if freq == "monthly":
        if getattr(recurrence, "month_end", False):
            return {"day": "last", "hour": hour, "minute": minute}, f"Oy oxirida {clock}"
        dom = getattr(recurrence, "day_of_month", None)
        dom = int(dom) if dom is not None else 1
        dom = min(max(dom, 1), 31)
        return {"day": dom, "hour": hour, "minute": minute}, f"Har oy {dom}-kun {clock}"

    return None, ""


async def _create_promise(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    deadline_dt = parse_uz_time(
        params.deadline, now, registry.settings.user_timezone
    )
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
        counterparty_id: int | None = None
        if params.counterparty_name:
            counterparty_id = await _resolve_or_create_id(
                registry, session, params.counterparty_name
            )
    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    task = await registry.task_service.create_self_promise(
        owner_id=owner_id,
        what=params.what,
        deadline_dt=deadline_dt,
        counterparty_id=counterparty_id,
        pre_alerts_minutes=params.pre_alerts_minutes,
        source=Source.nlu,
    )
    # Best-effort: also surface the deadline on the owner's Google Calendar.
    cal_link = await add_calendar_event(
        registry.calendar_service, title=params.what, start=deadline_dt
    )
    text = (
        f"🤝 Va'da yozib qo'yildi: {params.what}\n"
        f"🕒 Muddat: {_local(deadline_dt, registry)}"
    )
    if cal_link:
        text += "\n📅 Kalendarga ham qo'shildi."
    return DispatchResult(text, reply_markup=undo_button(KIND_PROMISE, task.id))


async def _assign_task_with_followup(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    deadline_dt = parse_uz_time(
        params.deadline, now, registry.settings.user_timezone
    )
    # Several namesakes -> numbered pick (same UX as send); unknown -> track it
    # against a lightweight contact so the follow-up still works.
    resolved = await _resolve_or_pend(
        registry,
        params.assignee_name,
        intent_name="assign_task_with_followup",
        params=params,
        field="assignee_name",
        required=False,
    )
    if isinstance(resolved, DispatchResult):
        return resolved

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
        if resolved is None:
            # Create a lightweight contact so the task can still be tracked.
            person = await person_repo.create(
                session, display_name=params.assignee_name
            )
            assignee_id = person.id
        else:
            assignee_id = resolved.person_id

    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    task = await registry.task_service.create_delegated(
        assignee_id=assignee_id,
        created_by_id=owner_id,
        task=params.task,
        deadline_dt=deadline_dt,
        pre_alert_owner_minutes=params.pre_alert_to_owner_minutes,
        followup_offsets_minutes=params.followup_offsets_minutes,
        auto_followup=params.auto_followup_to_assignee,
        source=Source.nlu,
    )
    return DispatchResult(
        f"✅ Topshiriq nazoratga olindi: {params.assignee_name} — {params.task}\n"
        f"🕒 Muddat: {_local(deadline_dt, registry)}",
        reply_markup=undo_button(KIND_TASK, task.id),
    )


async def _send_message(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    if not (params.content or "").strip():
        return DispatchResult("Xabar matni bo'sh. Nima yuborishni ayting.")
    resolved = await _resolve_or_pend(
        registry,
        params.recipient_name,
        intent_name="send_message",
        params=params,
        field="recipient_name",
    )
    if isinstance(resolved, DispatchResult):
        return resolved
    if resolved.chat_id is None:
        return DispatchResult(
            f"{resolved.display_name} uchun Telegram identifikatori yo'q, "
            "xabar yuborib bo'lmadi."
        )

    # Honour an explicit "ovozli"/"matn" if the owner said one; otherwise let
    # them pick via buttons. Either way the send runs through complete_outbound.
    is_owner = resolved.chat_id == registry.settings.owner_chat_id
    owner_key = registry.settings.owner_chat_id
    _PENDING_OUT[owner_key] = _PendingOutbound(
        kind="send",
        recipient_id=resolved.person_id,
        display_name=resolved.display_name,
        content=params.content,
        is_owner=is_owner,
    )
    explicit = _explicit_send_mode(params.delivery)
    if explicit is not None:
        return await complete_outbound(registry, owner_key, explicit)
    return _outbound_prompt(resolved.display_name, params.content)


async def _schedule_message(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    if not (params.content or "").strip():
        return DispatchResult("Xabar matni bo'sh. Nima yuborishni ayting.")
    send_at = parse_uz_time(
        params.when, now, registry.settings.user_timezone, require_clock=True
    )
    resolved = await _resolve_or_pend(
        registry,
        params.recipient_name,
        intent_name="schedule_message",
        params=params,
        field="recipient_name",
    )
    if isinstance(resolved, DispatchResult):
        return resolved

    # A meeting notice is delivered now AND again at the meeting time; for an
    # online Meet we also mint a Google Meet link and weave it into the message.
    meeting_notice = bool(getattr(params, "meeting_notice", False))
    content = params.content
    if meeting_notice and getattr(params, "create_meet_link", False):
        content = await _embed_meet_link(registry, content, start_at=send_at)

    # Honour an explicit channel if given; else ask. ``complete_outbound`` then
    # schedules the message for ``send_at`` (and sends it now for a notice) with
    # the chosen delivery mode.
    is_owner = resolved.chat_id == registry.settings.owner_chat_id
    owner_key = registry.settings.owner_chat_id
    _PENDING_OUT[owner_key] = _PendingOutbound(
        kind="schedule",
        recipient_id=resolved.person_id,
        display_name=resolved.display_name,
        content=content,
        is_owner=is_owner,
        send_at=send_at,
        also_send_now=meeting_notice,
    )
    explicit = _explicit_send_mode(params.delivery)
    if explicit is not None:
        return await complete_outbound(registry, owner_key, explicit)
    return _outbound_prompt(resolved.display_name, content)


async def _embed_meet_link(
    registry: ServiceRegistry, content: str, *, start_at: datetime
) -> str:
    """Append a freshly minted Google Meet link to ``content`` (best-effort).

    Mirrors the meeting flow: when Google Calendar is connected, create a Meet
    link for a 30-minute slot at ``start_at`` and add it to the message. On any
    failure (no Google, auth expired) the message goes out without a link rather
    than blocking the send.
    """
    cal = registry.calendar_service
    if cal is None or not cal.available():
        return content
    try:
        from app.integrations.google.meet import create_meet_link

        meet_link, _event_id = await create_meet_link(
            cal,
            title="Uchrashuv",
            start=start_at,
            end=start_at + timedelta(minutes=30),
        )
    except Exception:  # noqa: BLE001 - degrade to a linkless message
        logger.exception("schedule_message.meet_link.failed")
        return content
    if not meet_link:
        return content
    return f"{content}\n🔗 Meet: {meet_link}"


async def _add_finance(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    # Validate the amount: a zero/negative debt is almost always a parse slip,
    # so reject it with a clear ask instead of silently recording nonsense.
    if params.amount is None or params.amount <= 0:
        return DispatchResult(
            "Summa noto'g'ri. Iltimos, musbat miqdorni ayting "
            "(masalan «Karimga 200 ming so'm qarz berdim»)."
        )
    currency = (params.currency or "UZS").strip().upper() or "UZS"
    # A debt's due date is OPTIONAL: parse it only when a real phrase is given,
    # and never let a vague/empty time block recording the debt.
    due_dt: datetime | None = None
    due = params.due
    raw = (getattr(due, "raw", "") or "").strip() if due is not None else ""
    if raw:
        try:
            due_dt = parse_uz_time(due, now, registry.settings.user_timezone)
        except AmbiguousTime:
            due_dt = None

    async with registry.session() as session:
        counterparty_id = await _resolve_or_create_id(
            registry, session, params.counterparty_name
        )

    # debt = the owner owes them; credit = they owe the owner.
    if params.direction == "debt":
        direction = DebtDirection.i_owe_them
    else:
        direction = DebtDirection.they_owe_me

    record = await registry.finance_service.add_entry(
        counterparty_id=counterparty_id,
        direction=direction,
        amount=params.amount,
        currency=currency,
        due_dt=due_dt,
        description=params.note,
    )

    amount_str = f"{params.amount:g}"
    if direction == DebtDirection.they_owe_me:
        line = f"{params.counterparty_name} sizga {amount_str} {currency} qarzdor."
    else:
        line = f"Siz {params.counterparty_name}ga {amount_str} {currency} qarzdorsiz."
    text = f"💰 Qarz yozib qo'yildi: {line}"
    text += f"\n📝 Berilgan sana: {_local(record.incurred_at, registry)}"
    if due_dt is not None:
        text += f"\n🕒 To'lov muddati: {_local(due_dt, registry)}"
    return DispatchResult(text, reply_markup=undo_button(KIND_FINANCE, record.id))


async def _cancel_item(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    # Best-effort: a numeric selector lets us cancel a known row directly.
    selector = (params.selector or "").strip()
    kind = params.item_kind
    if selector.isdigit():
        row_id = int(selector)
        if kind == "reminder":
            await registry.reminder_service.cancel(row_id)
            return DispatchResult("Eslatma bekor qilindi.")
        if kind in ("promise", "followup"):
            await registry.task_service.cancel(row_id)
            return DispatchResult("Vazifa bekor qilindi.")
        if kind == "message":
            ok = await registry.message_service.cancel(row_id)
            return DispatchResult(
                "Xabar bekor qilindi."
                if ok
                else "Bunday xabar topilmadi yoki allaqachon yuborilgan."
            )
        if kind == "meeting":
            ok = await registry.meeting_service.cancel(row_id)
            return DispatchResult(
                "Uchrashuv bekor qilindi."
                if ok
                else "Bunday uchrashuv topilmadi."
            )
    return DispatchResult(
        "Bekor qilish uchun aniqroq ma'lumot kerak "
        "(masalan, elementning raqami)."
    )


async def _schedule_meeting(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Schedule a meeting (optionally with a Meet link) + 30/15/0 alerts."""
    start_at = parse_uz_time(
        params.when, now, registry.settings.user_timezone, require_clock=True
    )
    end_at = start_at + timedelta(minutes=params.duration_minutes or 30)

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    # Resolve who receives the Meet link at start. Same-name contacts (e.g. a
    # Latin and a Cyrillic "Akmal") trigger a numbered pick that resumes THIS
    # meeting once the owner chooses.
    notify_kind: NotifyTargetKind | None = None
    notify_ref: str | None = None
    target_name: str | None = None
    if params.notify_target_name:
        resolved = await _resolve_or_pend(
            registry,
            params.notify_target_name,
            intent_name="schedule_meeting",
            params=params,
            field="notify_target_name",
            required=False,
        )
        if isinstance(resolved, DispatchResult):
            return resolved
        if resolved is not None:
            notify_kind = NotifyTargetKind.person
            notify_ref = str(resolved.person_id)
            target_name = resolved.display_name

    # Best-effort Google Meet link (when Google is configured).
    meet_link: str | None = None
    gcal_event_id: str | None = None
    no_link_note = ""
    cal = registry.calendar_service
    if params.create_meet_link:
        if cal is not None and cal.available():
            from app.integrations.google.meet import create_meet_link

            try:
                meet_link, gcal_event_id = await create_meet_link(
                    cal, title=params.title, start=start_at, end=end_at
                )
            except Exception as exc:  # noqa: BLE001 - degrade to a linkless meeting
                logger.exception("meeting.meet_link.failed")
                if _is_google_auth_error(exc):
                    no_link_note = (
                        "\n(Google ruxsati tugagan — qayta ulang: "
                        "python -m scripts.google_auth)"
                    )
                else:
                    no_link_note = "\n(Meet havolasini yaratib bo'lmadi.)"
        else:
            no_link_note = "\n(Google ulanmagani uchun Meet havolasi yaratilmadi.)"

    meeting = await registry.meeting_service.create_meeting(
        owner_id=owner_id,
        title=params.title,
        start_at=start_at,
        end_at=end_at,
        meet_link=meet_link,
        notify_target_kind=notify_kind,
        notify_target_ref=notify_ref,
        gcal_event_id=gcal_event_id,
    )

    text = (
        f"📅 Uchrashuv rejalashtirildi: {params.title}\n"
        f"🕒 Vaqt: {_local(start_at, registry)}"
    )
    if meet_link:
        text += f"\n🔗 Meet: {meet_link}"
    text += no_link_note
    text += "\n⏰ 1 kun va 1 soat oldin eslataman."
    if notify_kind is not None and meet_link:
        text += f"\n📨 Havola boshlanishida {target_name}ga yuboriladi."
    return DispatchResult(text, reply_markup=undo_button(KIND_MEETING, meeting.id))


async def _find_free_slots(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Propose free calendar slots for the requested day."""
    cal = registry.calendar_service
    if cal is None or not cal.available():
        return DispatchResult(
            "Google Calendar ulanmagan. Bo'sh vaqtlarni ko'rsata olmayman — "
            ".env faylda Google sozlamalarini to'ldiring."
        )

    tz = ZoneInfo(registry.settings.user_timezone)
    anchor = parse_uz_time(params.date_range, now, registry.settings.user_timezone)
    day = anchor.astimezone(tz).date()
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    try:
        slots = await cal.find_free_slots(
            start=day_start,
            end=day_end,
            duration_minutes=params.duration_minutes or 30,
        )
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        logger.exception("free_slots.failed")
        if _is_google_auth_error(exc):
            return DispatchResult(_GOOGLE_REAUTH)
        return DispatchResult("Bo'sh vaqtlarni olishda xatolik yuz berdi.")
    if not slots:
        return DispatchResult(
            f"{day.strftime('%d.%m')} uchun bo'sh vaqt topilmadi."
        )

    lines = [f"{day.strftime('%d.%m')} uchun bo'sh vaqtlar:"]
    for slot_start, slot_end in slots[:6]:
        s = slot_start.astimezone(tz).strftime("%H:%M")
        e = slot_end.astimezone(tz).strftime("%H:%M")
        lines.append(f"• {s}–{e}")
    return DispatchResult("\n".join(lines))


async def _get_digest(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Build and return a channel digest of the most popular recent posts."""
    digest = registry.digest_service
    if digest is None:
        return DispatchResult("Dayjest xizmati hozircha mavjud emas.")
    top_n = params.top_n or registry.settings.digest_default_top_n
    summary = await digest.run(top_n=top_n, deliver=False)
    if not summary:
        return DispatchResult(
            "📭 Hozircha dayjest uchun material yo'q.\n"
            "Dayjest siz a'zo bo'lgan Telegram <b>kanallari</b>ning eng faol "
            "postlaridan tuziladi. Buning uchun: bot ulangan akkaunt orqali "
            "kerakli yangiliklar kanallariga a'zo bo'ling — keyingi postlar "
            "avtomatik yig'iladi va dayjestga tushadi.",
            parse_mode="HTML",
        )
    return DispatchResult(summary, parse_mode="HTML")


# ── listing / viewing handlers (read-only) ────────────────────────────────────
def _fmt_amount(amount: Decimal) -> str:
    """Format a money amount without trailing zeros, space-grouped thousands."""
    normalized = amount.normalize()
    _sign, _digits, exponent = normalized.as_tuple()
    if exponent >= 0:
        return f"{int(normalized):,}".replace(",", " ")
    return f"{normalized:,.2f}".replace(",", " ")


def _money_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Render debt rows as an aligned monospace table in a Telegram <pre> block.

    ``rows`` are ``(name, amount_str, currency, due_str)``. Columns are padded so
    names left-align and amounts right-align — table-like on every device.
    """
    name_w = min(max((len(n) for n, _, _, _ in rows), default=4), 16)
    amt_w = max((len(a) for _, a, _, _ in rows), default=3)
    lines = []
    for i, (name, amount, currency, due) in enumerate(rows, start=1):
        shown = name if len(name) <= name_w else name[: name_w - 1] + "…"
        line = f"{i}. {shown:<{name_w}}  {amount:>{amt_w}} {currency}"
        if due:
            line += f"  📅{due}"
        lines.append(line)
    return "<pre>" + html.escape("\n".join(lines), quote=False) + "</pre>"


def _contact_list_prompt(
    name: str, candidates: list[ContactMatch], *, total: int | None = None
) -> str:
    """Numbered 'which contact?' prompt for a name search, framed for messaging."""
    shown = len(candidates)
    count = total if total is not None else shown
    if count == 1:
        head = f"«{name}» bo'yicha 1 ta kontakt topildi. Kimga xabar yuboramiz?"
    else:
        head = (
            f"«{name}» bo'yicha {count} ta kontakt topildi. "
            "Kimga xabar yuboramiz? Raqamini tanlang:"
        )
    lines = [head]
    for i, candidate in enumerate(candidates, start=1):
        line = f"{i}. {candidate.display_name} — {_candidate_detail(candidate)}"
        if candidate.chat_id is None:
            line += " ⚠️ Telegram ID yo'q"
        lines.append(line)
    if shown < count:
        lines.append(
            f"… va yana {count - shown} ta. Aniqroq ism aytsangiz, ro'yxat qisqaradi."
        )
    return "\n".join(lines)


async def _list_contacts(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's saved contacts.

    A NAME query becomes a numbered, tappable pick list (search across ALL
    contacts, honorific/plural-tolerant): choosing one starts composing a
    message to that contact. With no query, show the read-only full address book.
    """
    from app.brain.contacts import clean_contact_query

    limit = params.limit if getattr(params, "limit", 0) and params.limit > 0 else 40

    # ── name search -> numbered pick that leads into composing a message ──────
    if params.query:
        cleaned = clean_contact_query(params.query)
        async with registry.session() as session:
            people = await person_repo.search_by_name(session, cleaned)
        people = [p for p in people if not p.is_owner]
        if not people:
            return DispatchResult(
                f"«{cleaned}» bo'yicha kontakt topilmadi. "
                "Ismni boshqacha (to'liqroq yoki qisqaroq) ayting."
            )
        total = len(people)
        candidates = _order_candidates(
            [_match_from_person(p) for p in people], limit=_MAX_CONTACT_LIST
        )
        _PENDING[registry.settings.owner_chat_id] = _PendingChoice(
            intent_name="compose_pick",
            params=None,
            field="",
            candidate_ids=[c.person_id for c in candidates],
            candidate_labels=[c.display_name for c in candidates],
        )
        return DispatchResult(
            _contact_list_prompt(cleaned, candidates, total=total),
            reply_markup=contact_pick_keyboard([c.person_id for c in candidates]),
        )

    # ── no query: read-only full address book ────────────────────────────────
    async with registry.session() as session:
        people = await person_repo.list_all(session)
    people = [p for p in people if not p.is_owner]
    total = len(people)
    if total == 0:
        return DispatchResult(
            "Hozircha kontaktlar yo'q. Userbot ulanganda avtomatik sinxronlanadi."
        )
    shown = people[:limit]
    if total > len(shown):
        header = f"👥 <b>Kontaktlaringiz</b> — {total} tadan {len(shown)} tasi"
    else:
        header = f"👥 <b>Kontaktlaringiz</b> — {total} ta"
    rows = []
    for i, person in enumerate(shown, start=1):
        row = f"{i}. <b>{html.escape(person.display_name or '', quote=False)}</b>"
        if person.telegram_username:
            row += f"  @{html.escape(person.telegram_username, quote=False)}"
        if person.phone:
            row += f"  📞 {html.escape(person.phone, quote=False)}"
        rows.append(row)
    # Expandable blockquote keeps a long contact list tidy/collapsible.
    text = f"{header}\n<blockquote expandable>{chr(10).join(rows)}</blockquote>"
    if total > len(shown):
        text += (
            f"\n… va yana {total - len(shown)} ta. Aniqroq topish uchun ism ayting."
        )
    return DispatchResult(text, parse_mode="HTML")


async def _list_finance(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List outstanding debts/credits with a per-currency total."""
    wanted: list[tuple[str, DebtDirection]] = []
    if params.direction in ("they_owe_me", "all"):
        wanted.append(("💰 <b>Sizga qarzdorlar</b>", DebtDirection.they_owe_me))
    if params.direction in ("i_owe_them", "all"):
        wanted.append(("💸 <b>Siz qarzdorsiz</b>", DebtDirection.i_owe_them))

    blocks: list[str] = []
    settle_items: list[tuple[int, str]] = []  # (record_id, name) for the «✅» buttons
    any_records = False
    async with registry.session() as session:
        for header, direction in wanted:
            records = await finance_repo.list_open(session, direction=direction)
            if not records:
                blocks.append(f"{header}\n<i>— yo'q</i>")
                continue
            any_records = True
            rows: list[tuple[str, str, str, str]] = []
            totals: dict[str, Decimal] = {}
            for record in records:
                counterparty = await person_repo.get_by_id(
                    session, record.counterparty_id
                )
                name = getattr(counterparty, "display_name", "kishi")
                due = _local(record.due_at, registry) if record.due_at else ""
                rows.append(
                    (name, _fmt_amount(record.amount), record.currency, due)
                )
                settle_items.append((record.id, name))
                totals[record.currency] = (
                    totals.get(record.currency, Decimal(0)) + record.amount
                )
            total_str = ", ".join(
                f"{_fmt_amount(value)} {cur}" for cur, value in totals.items()
            )
            blocks.append(
                f"{header}\n{_money_table(rows)}\n💵 <b>Jami: {total_str}</b>"
            )

    if not blocks:
        return DispatchResult("Hozircha qarz yozuvlari yo'q.")
    if not any_records:  # only "— yo'q" placeholders, nothing to settle
        return DispatchResult("\n\n".join(blocks), parse_mode="HTML")

    text = (
        "\n\n".join(blocks)
        + "\n\n✅ To'langanini quyidagi tugma bilan belgilang — ro'yxatdan tushadi."
    )
    dir_code = {"they_owe_me": "t", "i_owe_them": "i", "all": "a"}.get(
        params.direction, "a"
    )
    return DispatchResult(
        text,
        parse_mode="HTML",
        reply_markup=debt_settle_keyboard(settle_items, dir_code),
    )


async def _list_agenda(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's plan: reminders, promises, tracked tasks, meetings."""
    tz = registry.settings.user_timezone
    today_only = params.scope == "today"
    local_today = now.astimezone(ZoneInfo(tz)).date()

    def _is_today(dt: datetime | None) -> bool:
        return dt is not None and as_utc(dt).astimezone(ZoneInfo(tz)).date() == local_today

    def _is_past(dt: datetime | None) -> bool:
        return dt is not None and as_utc(dt).astimezone(ZoneInfo(tz)).date() < local_today

    def _is_past_time(dt: datetime | None) -> bool:
        return dt is not None and as_utc(dt) < as_utc(now)

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            return DispatchResult("Egasi topilmadi. Avval /start yuboring.")
        owner_id = owner.id
        reminders = await reminder_repo.list_active(session, owner_id)
        promises = await task_repo.list_open(
            session, owner_id=owner_id, kind=TaskKind.self_promise
        )
        delegated_rows = await task_repo.list_open(session, kind=TaskKind.delegated)
        delegated: list[tuple[str, Any]] = []
        for task in delegated_rows:
            if task.created_by_id != owner_id:
                continue
            assignee = await person_repo.get_by_id(session, task.owner_id)
            delegated.append((getattr(assignee, "display_name", "kishi"), task))
        meetings = await meeting_repo.list_upcoming(session, owner_id)

    # Personal reminders are TRANSIENT: once their time has passed they drop out
    # of the plan entirely (they are not "overdue" work to chase). Recurring
    # reminders (due_at = next occurrence) always stay.
    reminders = [
        r
        for r in reminders
        if r.recurrence or r.due_at is None or not _is_past_time(r.due_at)
    ]

    # Overdue (past, still-pending) WORK — promises + tracked tasks only — so it
    # surfaces in both the "today" and "all" views. Reminders are excluded above.
    overdue: list[str] = []
    overdue += [p.title for p in promises if _is_past(p.due_at)]
    overdue += [f"{n}: {t.title}" for n, t in delegated if _is_past(t.due_at)]

    if today_only:
        reminders = [r for r in reminders if _is_today(r.due_at)]
        promises = [p for p in promises if _is_today(p.due_at)]
        delegated = [(n, t) for n, t in delegated if _is_today(t.due_at)]
        meetings = [m for m in meetings if _is_today(m.start_at)]

    def fmt(dt: datetime | None) -> str:
        return _local(dt, registry) if dt is not None else "—"

    def _section(emoji: str, label: str, items: list[str]) -> str:
        body = "\n".join(items)
        return f"{emoji} <b>{label} ({len(items)})</b>\n<blockquote>{body}</blockquote>"

    def esc(text: str) -> str:
        return html.escape(text, quote=False)

    blocks: list[str] = []
    if overdue:
        blocks.append(
            _section(
                "⚠️",
                "Muddati o'tgan",
                [f"• {esc(title)}" for title in overdue],
            )
        )
    if reminders:
        blocks.append(
            _section(
                "⏰",
                "Eslatmalar",
                [f"• {esc(r.title)} — {fmt(r.due_at)}" for r in reminders],
            )
        )
    if promises:
        blocks.append(
            _section(
                "🤝",
                "Va'dalar",
                [f"• {esc(p.title)} — {fmt(p.due_at)}" for p in promises],
            )
        )
    if delegated:
        blocks.append(
            _section(
                "✅",
                "Nazoratdagi topshiriqlar",
                [
                    f"• <b>{esc(name)}</b> — {esc(t.title)} — {fmt(t.due_at)}"
                    for name, t in delegated
                ],
            )
        )
    if meetings:
        blocks.append(
            _section(
                "📅",
                "Uchrashuvlar",
                [f"• {esc(m.title)} — {fmt(m.start_at)}" for m in meetings],
            )
        )

    if not blocks:
        return DispatchResult(
            "Rejangiz bo'sh — bugun hech narsa yo'q."
            if today_only
            else "Rejangiz bo'sh — hozircha hech narsa yo'q."
        )
    title = "📋 <b>Bugungi rejangiz</b>" if today_only else "📋 <b>Rejangiz</b>"
    return DispatchResult(title + "\n\n" + "\n\n".join(blocks), parse_mode="HTML")


async def _list_reminders(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's active reminders: upcoming one-shots + recurring ones."""
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            return DispatchResult("Egasi topilmadi. Avval /start yuboring.")
        reminders = await reminder_repo.list_active(session, owner.id)

    # Transient: a one-shot reminder whose time has passed drops out of the list.
    reminders = [
        r
        for r in reminders
        if r.recurrence or r.due_at is None or as_utc(r.due_at) >= as_utc(now)
    ]
    if not reminders:
        return DispatchResult(
            "⏰ Hozircha eslatma yo'q.\n«➕ Yangi eslatma» bilan qo'shing yoki "
            "«3 kundan keyin hujjatni yubor» deb yozing."
        )

    reminders.sort(
        key=lambda r: (r.due_at is None, as_utc(r.due_at) if r.due_at else as_utc(now))
    )
    lines: list[str] = []
    for r in reminders:
        title = html.escape(r.title, quote=False)
        if r.recurrence:
            lines.append(f"🔁 {title} — {html.escape(r.recurrence, quote=False)}")
        else:
            lines.append(f"⏰ {title} — {_local(r.due_at, registry)}")
    text = (
        f"⏰ <b>Eslatmalarim ({len(reminders)})</b>\n"
        f"<blockquote expandable>{chr(10).join(lines)}</blockquote>"
    )
    return DispatchResult(text, parse_mode="HTML")


async def _list_meetings(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's scheduled meetings (with Meet links), HTML-formatted."""
    tz = registry.settings.user_timezone
    today_only = getattr(params, "scope", "all") == "today"
    local_today = now.astimezone(ZoneInfo(tz)).date()

    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            return DispatchResult("Egasi topilmadi. Avval /start yuboring.")
        meetings = await meeting_repo.list_upcoming(session, owner.id)

    if today_only:
        meetings = [
            m
            for m in meetings
            if m.start_at is not None
            and as_utc(m.start_at).astimezone(ZoneInfo(tz)).date() == local_today
        ]
    if not meetings:
        return DispatchResult("📅 Hozircha rejalashtirilgan uchrashuv yo'q.")

    items = []
    for meeting in meetings:
        line = (
            f"• <b>{html.escape(meeting.title, quote=False)}</b> "
            f"— {_local(meeting.start_at, registry)}"
        )
        if meeting.meet_link:
            line += (
                "\n  🔗 <a href=\""
                f"{html.escape(meeting.meet_link, quote=True)}\">Meet havola</a>"
            )
        items.append(line)
    text = (
        f"📅 <b>Uchrashuvlaringiz ({len(meetings)})</b>\n"
        f"<blockquote>{chr(10).join(items)}</blockquote>"
    )
    return DispatchResult(text, parse_mode="HTML")


# ── important dates / birthdays ───────────────────────────────────────────────
async def _add_important_date(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Save an important date / birthday with day-before reminders."""
    if not (1 <= int(params.month) <= 12) or not (1 <= int(params.day) <= 31):
        return DispatchResult(
            "Sana noto'g'ri. Oy (1-12) va kunni (1-31) aniqroq ayting."
        )
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    try:
        category = EventCategory(params.category)
    except ValueError:
        category = EventCategory.other

    from app.services.event_service import category_icon

    event = await registry.event_service.add_event(
        owner_id=owner_id,
        title=params.title,
        category=category,
        month=int(params.month),
        day=int(params.day),
        year=params.year,
        yearly=params.yearly,
        remind_days_before=params.remind_days_before,
    )

    icon = category_icon(category)
    fmt = "%d.%m.%Y" if not event.yearly else "%d.%m"
    date_str = event.event_date.strftime(fmt)
    days = ", ".join(f"{d}" for d in (event.remind_days_before or [1]))
    text = f"{icon} Muhim sana saqlandi: {params.title}\n📅 Sana: {date_str}"
    if event.yearly:
        text += " (har yili)"
    text += f"\n🔔 {days} kun oldin eslataman."
    return DispatchResult(text, reply_markup=undo_button(KIND_EVENT, event.id))


async def _list_important_dates(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's saved important dates with a 'days left' countdown."""
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            return DispatchResult("Egasi topilmadi. Avval /start yuboring.")
        events = await registry.event_service.list_active(owner.id)

    if not events:
        return DispatchResult(
            "📆 Hozircha muhim sana yo'q. Masalan: «5-avgust Alining tug'ilgan kuni»."
        )

    from app.services.event_service import category_icon

    items: list[str] = []
    for event in events:
        icon = category_icon(event.category)
        date_str = event.event_date.strftime("%d.%m")
        left = ""
        if event.next_fire_at is not None:
            days_left = (as_utc(event.next_fire_at) - as_utc(now)).days
            if days_left == 0:
                left = " — <b>bugun!</b>"
            elif days_left > 0:
                left = f" — {days_left} kun qoldi"
        title = html.escape(event.title, quote=False)
        items.append(f"{icon} <b>{title}</b> — {date_str}{left}")

    body = "\n".join(items)
    text = (
        f"📆 <b>Muhim sanalar ({len(events)})</b>\n"
        f"<blockquote expandable>{body}</blockquote>"
    )
    return DispatchResult(text, parse_mode="HTML")


# ── decisions journal ─────────────────────────────────────────────────────────
async def _log_decision(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Record a personal decision in the journal."""
    if not (params.text or "").strip():
        return DispatchResult("Qaror matni bo'sh. Qaroringizni ayting.")
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        owner_id = owner.id if owner is not None else None
    if owner_id is None:
        return DispatchResult("Egasi topilmadi. Avval /start buyrug'ini yuboring.")

    decision = await registry.decision_service.add(
        owner_id=owner_id, text=params.text, tag=params.tag
    )
    return DispatchResult(
        f"📓 Qaror jurnalga yozildi:\n«{params.text}»",
        reply_markup=undo_button(KIND_DECISION, decision.id),
    )


async def _list_decisions(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """List the owner's recent journalled decisions (newest first)."""
    limit = params.limit if getattr(params, "limit", 0) and params.limit > 0 else 20
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
        if owner is None:
            return DispatchResult("Egasi topilmadi. Avval /start yuboring.")
        decisions = await registry.decision_service.list_recent(owner.id, limit=limit)

    if not decisions:
        return DispatchResult(
            "📓 Qarorlar arxivi bo'sh. Masalan: «bugun qaror qildim: ...»."
        )

    items: list[str] = []
    for decision in decisions:
        when = _local(decision.decided_at, registry)
        text = html.escape(decision.text, quote=False)
        tag = f" #{html.escape(decision.tag, quote=False)}" if decision.tag else ""
        items.append(f"• <i>{when}</i>{tag}\n{text}")

    body = "\n\n".join(items)
    out = (
        f"📓 <b>Qarorlar arxivi ({len(decisions)})</b>\n"
        f"<blockquote expandable>{body}</blockquote>"
    )
    return DispatchResult(out, parse_mode="HTML")


# ── Gmail (read-only) ─────────────────────────────────────────────────────────
async def _list_emails(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Show the owner's recent important / unread Gmail messages."""
    gmail = registry.gmail_service
    if gmail is None or not gmail.available():
        return DispatchResult(
            "📧 Gmail ulanmagan. .env da Google sozlamalarini to'ldiring va "
            "gmail.readonly ruxsati uchun «python -m scripts.google_auth» ni "
            "qayta ishga tushiring."
        )
    limit = params.limit if getattr(params, "limit", 0) and params.limit > 0 else 5
    try:
        emails = await gmail.list_unread(
            max_results=min(limit, registry.settings.gmail_max_results or 5)
        )
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        logger.exception("gmail.list.failed")
        if _is_google_auth_error(exc):
            return DispatchResult(
                "Gmail ruxsati yo'q yoki tugagan. «python -m scripts.google_auth» "
                "ni qayta ishga tushiring (gmail.readonly ruxsatini bering)."
            )
        return DispatchResult("Xatlarni olishda xatolik yuz berdi.")

    if not emails:
        return DispatchResult("📭 O'qilmagan muhim xat yo'q. Hammasi nazoratda!")

    items = []
    for e in emails:
        mark = "⭐ " if e.important else ""
        sender = html.escape(e.sender, quote=False)
        subject = html.escape(e.subject, quote=False)
        snippet = html.escape(e.snippet, quote=False)
        items.append(f"{mark}<b>{sender}</b>\n{subject}\n<i>{snippet}</i>")
    body = "\n\n".join(items)
    text = (
        f"📧 <b>O'qilmagan xatlar ({len(emails)})</b>\n"
        f"<blockquote expandable>{body}</blockquote>"
    )
    return DispatchResult(text, parse_mode="HTML")


# ── Notion ──────────────────────────────────────────────────────────────────
async def _save_to_notion(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Save a free-form note / plan to the owner's Notion workspace."""
    if not (params.text or "").strip():
        return DispatchResult("Saqlanadigan matn bo'sh. Nimani saqlashni ayting.")
    notion = registry.notion_service
    if notion is None or not notion.available():
        return DispatchResult(
            "📝 Notion ulanmagan. .env da NOTION_API_KEY va NOTION_PARENT_PAGE_ID "
            "ni to'ldiring (integratsiyani o'sha sahifaga ulashing)."
        )
    try:
        await notion.save_note(text=params.text, title=params.title)
    except Exception as exc:  # noqa: BLE001 - surface a clear message
        logger.exception("notion.save.failed")
        return DispatchResult(
            f"Notion'ga saqlab bo'lmadi. Sozlamalarni tekshiring.\n({str(exc)[:120]})"
        )
    return DispatchResult("📝 Notion'ga saqlandi.")


# ── Google Calendar view ──────────────────────────────────────────────────────
_WEEKDAYS_UZ = (
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
)


async def _show_calendar(
    registry: ServiceRegistry, params: Any, now: datetime
) -> DispatchResult:
    """Render the owner's Google Calendar (today or this week) as a tidy post."""
    cal = registry.calendar_service
    if cal is None or not cal.available():
        return DispatchResult(
            "📆 Google Calendar ulanmagan. .env faylda Google sozlamalarini "
            "to'ldiring."
        )
    tz = ZoneInfo(registry.settings.user_timezone)
    today = now.astimezone(tz).date()
    days = 1 if getattr(params, "scope", "week") == "today" else 7
    start = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=days)

    try:
        events = await cal.list_events(start=start, end=end)
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        logger.exception("calendar.list.failed")
        if _is_google_auth_error(exc):
            return DispatchResult(_GOOGLE_REAUTH)
        return DispatchResult("Kalendarni olishda xatolik yuz berdi.")

    if not events:
        return DispatchResult(
            "📆 Bugun kalendaringiz bo'sh."
            if days == 1
            else "📆 Bu hafta kalendaringiz bo'sh."
        )

    by_day: dict[Any, list[Any]] = {}
    for ev in events:
        d = as_utc(ev.start).astimezone(tz).date()
        by_day.setdefault(d, []).append(ev)

    if days == 1:
        header = (
            "📆 <b>Kalendar — Bugun</b>\n"
            f"<i>{_WEEKDAYS_UZ[today.weekday()]}, {today.strftime('%d.%m.%Y')}</i>"
        )
    else:
        rng_end = today + timedelta(days=6)
        header = (
            "📆 <b>Kalendar — Bu hafta</b>\n"
            f"<i>{today.strftime('%d.%m')} – {rng_end.strftime('%d.%m')}</i>"
        )

    blocks = [header]
    for i in range(days):
        d = today + timedelta(days=i)
        evs = sorted(by_day.get(d, []), key=lambda e: as_utc(e.start))
        if not evs:
            continue  # skip empty days for a clean, scannable post
        label = f"{_WEEKDAYS_UZ[d.weekday()]}, {d.strftime('%d.%m')}"
        if i == 0:
            label = "Bugun · " + label
        elif i == 1:
            label = "Ertaga · " + label
        lines = []
        for ev in evs[:8]:
            when = (
                "Kun bo'yi"
                if ev.all_day
                else as_utc(ev.start).astimezone(tz).strftime("%H:%M")
            )
            line = f"• <b>{when}</b> — {html.escape(ev.summary, quote=False)}"
            if ev.link:
                line += f' <a href="{html.escape(ev.link, quote=True)}">🔗</a>'
            lines.append(line)
        if len(evs) > 8:
            lines.append(f"… va yana {len(evs) - 8} ta")
        blocks.append(
            f"<b>📅 {html.escape(label, quote=False)}</b>\n"
            f"<blockquote>{chr(10).join(lines)}</blockquote>"
        )
    return DispatchResult("\n\n".join(blocks), parse_mode="HTML")


# ── shared resolution helper ──────────────────────────────────────────────────
async def _resolve_or_create_id(
    registry: ServiceRegistry, session: Any, name: str
) -> int:
    """Resolve ``name`` to a person id, creating a lightweight contact if absent.

    On a :class:`Disambiguation` the first candidate is taken (best-effort);
    callers that need to ask the owner should resolve explicitly instead.
    """
    resolved = await resolve_contact(session, name)
    if isinstance(resolved, ContactMatch):
        return resolved.person_id
    if isinstance(resolved, Disambiguation) and resolved.candidates:
        return resolved.candidates[0].person_id
    person = await person_repo.create(session, display_name=name)
    return person.id


# ── intent name -> handler map ────────────────────────────────────────────────
_HANDLERS = {
    "create_reminder": _create_reminder,
    "create_promise": _create_promise,
    "assign_task_with_followup": _assign_task_with_followup,
    "send_message": _send_message,
    "schedule_message": _schedule_message,
    "add_finance": _add_finance,
    "cancel_item": _cancel_item,
    "schedule_meeting": _schedule_meeting,
    "find_free_slots": _find_free_slots,
    "get_digest": _get_digest,
    "list_contacts": _list_contacts,
    "list_finance": _list_finance,
    "list_agenda": _list_agenda,
    "list_reminders": _list_reminders,
    "list_meetings": _list_meetings,
    "add_important_date": _add_important_date,
    "list_important_dates": _list_important_dates,
    "log_decision": _log_decision,
    "list_decisions": _list_decisions,
    "list_emails": _list_emails,
    "save_to_notion": _save_to_notion,
    "show_calendar": _show_calendar,
}
