"""Shared time-display helpers for services.

Datetimes are stored tz-aware UTC, but some backends (notably SQLite) drop the
tzinfo on read and return a *naive* datetime. To display correctly we treat any
naive value as UTC before converting to the user's local zone.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

_DISPLAY_FMT = "%d.%m %H:%M"
# Local hour a "move to tomorrow" / "remind tomorrow" lands on by default.
_TOMORROW_HOUR = 9


def as_utc(dt: datetime) -> datetime:
    """Return ``dt`` as tz-aware UTC, treating naive inputs as already UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_local_str(dt: datetime | None, tz_name: str, *, fmt: str = _DISPLAY_FMT) -> str:
    """Format ``dt`` in the user's local zone (empty string for ``None``)."""
    if dt is None:
        return ""
    local = as_utc(dt).astimezone(ZoneInfo(tz_name))
    return local.strftime(fmt)


def snooze_target(now: datetime, arg: str, tz_name: str) -> datetime:
    """Resolve a snooze/move button ``arg`` to a concrete UTC datetime.

    A numeric arg is a relative minute offset from ``now`` (e.g. ``"60"`` -> in
    one hour). The literal ``"tmrw"`` means tomorrow at 09:00 local time — the
    sensible landing spot for "remind me tomorrow" / "move to tomorrow".
    """
    if arg == "tmrw":
        tz = ZoneInfo(tz_name)
        local = as_utc(now).astimezone(tz)
        target = datetime.combine(
            local.date() + timedelta(days=1), time(_TOMORROW_HOUR, 0), tzinfo=tz
        )
        return target.astimezone(UTC)
    try:
        minutes = int(arg)
    except (TypeError, ValueError):
        minutes = 60
    return as_utc(now) + timedelta(minutes=max(1, minutes))
