"""Uzbek time-phrase parser.

Turns the verbatim time phrases captured by the NLU layer (``TimeSpec.raw`` or a
plain string) into a concrete tz-aware UTC ``datetime``, given a reference
``now``. The model never computes timestamps, so all the arithmetic lives here,
where it can be exhaustively unit-tested.

Supported forms (case-insensitive, in any combination that makes sense):
  * structured ``rel_minutes`` from the TimeSpec, when present and positive;
  * relative offsets: "(N) soat", "(N) (minut|min|daqiqa)", "yarim soat" (30m);
  * day words: "ertaga" (+1 day), "indinga"/"indin" (+2 days), "bugun" (today);
  * clock: "HH:MM", "soat HH", or a clock_hint like "09:00".

A bare hour 1-12 with no qualifier and no day word is ambiguous (could be today
or tomorrow, am or pm) and raises :class:`AmbiguousTime` rather than guessing.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo


class AmbiguousTime(Exception):
    """Raised when a phrase does not pin down a single concrete time.

    Carries WHAT is missing so the caller can ask precisely:
      * ``missing`` — "day", "clock", or "both".
      * ``clock`` — the known ``(hour, minute)`` when only the day is missing.
      * ``day_date`` — the known ``date`` when only the clock is missing.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: str = "both",
        clock: tuple[int, int] | None = None,
        day_date: date | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = missing
        self.clock = clock
        self.day_date = day_date


# Calendar-date entry: "15.03.2019", "15/03/2019", "15-03-2019", "15 03 2019",
# or ISO "2019-03-15". Used by the personal-data flow (passport/insurance dates).
_DMY_RE = re.compile(r"^\s*(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{4})\s*$")
_YMD_RE = re.compile(r"^\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*$")

# A fully-resolved local datetime "YYYY-MM-DD HH:MM", produced by the interactive
# day+time clarification flow and fed back through ``parse_uz_time`` as-is.
_ABS_DT_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})[ tT](\d{1,2}):(\d{2})\s*$")


def parse_date(text: str) -> date | None:
    """Parse a plain calendar date (day-first, or ISO) into a ``date``.

    Returns ``None`` when the text is not a valid date, so callers can re-prompt.
    """
    raw = (text or "").strip()
    match = _DMY_RE.match(raw)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        match = _YMD_RE.match(raw)
        if not match:
            return None
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


@runtime_checkable
class _TimeSpecLike(Protocol):
    """Duck-typed view of :class:`app.brain.intents.TimeSpec`."""

    raw: str
    rel_minutes: int | None
    kind: str


# ── regexes ────────────────────────────────────────────────────────────────
_HOURS_RE = re.compile(r"(\d+)\s*soat")
_MINUTES_RE = re.compile(r"(\d+)\s*(?:minut|min|daqiqa)")
_HALF_HOUR_RE = re.compile(r"yarim\s*soat")
_DAYS_RE = re.compile(r"(\d+)\s*kun")
_WEEKS_RE = re.compile(r"(\d+)\s*hafta")
_CLOCK_HHMM_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_CLOCK_SOAT_RE = re.compile(r"soat\s*(\d{1,2})(?!\s*soat)(?!\d)")
_BARE_HOUR_RE = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")

_DAY_WORDS = {
    "indinga": 2,
    "indin": 2,
    "ertaga": 1,
    "erta": 1,
    "bugun": 0,
}


def _coerce(spec: _TimeSpecLike | str) -> tuple[str, int | None]:
    """Return ``(raw_text_lower, rel_minutes)`` from a TimeSpec or plain str."""

    if isinstance(spec, str):
        return spec.strip().lower(), None
    raw = (getattr(spec, "raw", "") or "").strip().lower()
    rel = getattr(spec, "rel_minutes", None)
    return raw, rel


def _to_utc(dt: datetime, tzinfo: ZoneInfo) -> datetime:
    """Attach the user tz if naive, then convert to UTC."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    return dt.astimezone(UTC)


def _extract_day_offset(text: str) -> int | None:
    """Return a day offset for a day word in ``text``, or None if absent."""

    # Longest keys first so "indinga" wins over a hypothetical "indin" prefix.
    for word in sorted(_DAY_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return _DAY_WORDS[word]
    return None


def _extract_clock(text: str) -> tuple[int, int, bool] | None:
    """Return ``(hour, minute, explicit_minute)`` from a clock, or None.

    Order matters: an explicit ``HH:MM`` is tried first (minutes are explicit),
    then ``soat HH`` (minutes default to 0, not explicit). ``explicit_minute``
    lets the caller treat a bare ``soat 9`` (hour 1-12) as ambiguous.
    """

    m = _CLOCK_HHMM_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), True
    m = _CLOCK_SOAT_RE.search(text)
    if m:
        hour = int(m.group(1))
        if 0 <= hour <= 23:
            return hour, 0, False
    return None


def parse_uz_time(
    spec: _TimeSpecLike | str,
    now: datetime,
    tz: str = "Asia/Tashkent",
    *,
    require_clock: bool = False,
) -> datetime:
    """Parse an Uzbek time phrase into a tz-aware UTC datetime.

    Args:
        spec: A ``TimeSpec``-like object (``.raw``/``.rel_minutes``/``.kind``)
            or a plain string.
        now: Reference "now". May be naive (interpreted in ``tz``) or aware.
        tz: IANA timezone name for interpreting clock/day expressions.
        require_clock: When True, a bare day word with no clock ("ertaga",
            "bugun") is treated as ambiguous and raises :class:`AmbiguousTime`
            asking for the hour, instead of silently defaulting to 09:00. Set by
            callers that genuinely need a clock time (reminders, scheduled
            messages, meetings); left False for all-day uses (free-slot lookups,
            optional finance due dates).

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        AmbiguousTime: when the phrase does not resolve to a single time.
    """

    tzinfo = ZoneInfo(tz)
    # Work in the user's local wall-clock for day/clock math, then convert.
    now_local = now.astimezone(tzinfo) if now.tzinfo is not None else now.replace(tzinfo=tzinfo)

    text, rel_minutes = _coerce(spec)

    # 1) Structured relative offset wins outright.
    if rel_minutes is not None and rel_minutes > 0:
        return _to_utc(now_local + timedelta(minutes=rel_minutes), tzinfo)

    if not text:
        raise AmbiguousTime(
            "Vaqt ko'rsatilmagan. Iltimos, vaqtni aniqroq ayting.", missing="both"
        )

    # 1b) A fully-resolved "YYYY-MM-DD HH:MM" (from the clarification flow) is taken
    #     verbatim — both day and clock are already pinned down.
    iso = _ABS_DT_RE.match(text)
    if iso:
        y, mo, d, h, mi = (int(g) for g in iso.groups())
        return _to_utc(datetime(y, mo, d, h, mi), tzinfo)

    # 2) Pure relative offsets (hours / minutes / half hour). These may be
    #    combined ("1 soat 30 minut") and do not depend on day/clock words.
    delta = timedelta()
    has_relative = False
    if _HALF_HOUR_RE.search(text):
        delta += timedelta(minutes=30)
        has_relative = True
    for m in _HOURS_RE.finditer(text):
        delta += timedelta(hours=int(m.group(1)))
        has_relative = True
    for m in _MINUTES_RE.finditer(text):
        delta += timedelta(minutes=int(m.group(1)))
        has_relative = True

    # Day/week counts ("3 kun", "2 hafta") add whole days, like a day word.
    day_count = 0
    for m in _WEEKS_RE.finditer(text):
        day_count += 7 * int(m.group(1))
    for m in _DAYS_RE.finditer(text):
        day_count += int(m.group(1))

    day_offset = _extract_day_offset(text)
    clock = _extract_clock(text)

    # Combined day shift from day words + day/week counts.
    has_day = day_offset is not None or day_count > 0
    total_days = (day_offset or 0) + day_count

    # 3) A clock time (optionally with a day shift) -> absolute wall-clock.
    if clock is not None:
        hour, minute, explicit_minute = clock
        # Clock known but NO day. A bare "soat 9" is always ambiguous (am/pm + day);
        # and when the caller needs a precise day (reminders/tasks/meetings), even a
        # definite "soat 22:00" must pin a day rather than silently assume today.
        bare_ampm = not explicit_minute and 1 <= hour <= 12
        if not has_day and (require_clock or bare_ampm):
            raise AmbiguousTime(
                "Qaysi kun? Kunni tanlang.",
                missing="day",
                clock=(hour, minute),
            )
        base_day = now_local.date() + timedelta(days=total_days)
        candidate = datetime.combine(base_day, time(hour, minute), tzinfo=tzinfo)
        # No day shift and the time already passed today => assume next day.
        if not has_day and candidate <= now_local:
            candidate = candidate + timedelta(days=1)
        return _to_utc(candidate, tzinfo)

    # 4) A day shift with no clock. Callers that need a real clock time ask for
    #    one instead of inventing 09:00; all-day callers keep the sane default.
    if has_day and not has_relative:
        base_day = now_local.date() + timedelta(days=total_days)
        if require_clock:
            raise AmbiguousTime(
                "Soat nechada? Masalan: «15:00» yoki «9:30» deb yozing.",
                missing="clock",
                day_date=base_day,
            )
        candidate = datetime.combine(base_day, time(9, 0), tzinfo=tzinfo)
        return _to_utc(candidate, tzinfo)

    # 5) Relative offset (optionally combined with a day shift that adds days).
    if has_relative:
        result = now_local + delta + timedelta(days=total_days)
        return _to_utc(result, tzinfo)

    # 6) A bare hour with nothing else: the clock is known, the day is not.
    bare = _BARE_HOUR_RE.search(text)
    if bare and 0 <= int(bare.group(1)) <= 23:
        raise AmbiguousTime(
            "Qaysi kun? Kunni tanlang.",
            missing="day",
            clock=(int(bare.group(1)), 0),
        )

    raise AmbiguousTime(
        "Vaqtni tushunolmadim. Iltimos, qaytadan, aniqroq ayting "
        "(masalan ‘10 minutda’ yoki ‘ertaga soat 9’).",
        missing="both",
    )
