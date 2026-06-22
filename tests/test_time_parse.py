"""Unit tests for the Uzbek time-phrase parser (pure, no DB / no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.brain.intents import TimeSpec
from app.brain.time_parse import AmbiguousTime, parse_uz_time

# A fixed reference "now": 2026-06-18 08:00 UTC == 13:00 Asia/Tashkent (+5).
NOW = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
TZ = "Asia/Tashkent"


def test_relative_minutes():
    got = parse_uz_time(TimeSpec(raw="10 minutda"), NOW, TZ)
    assert got == NOW + timedelta(minutes=10)


def test_relative_hours_and_minutes_combined():
    got = parse_uz_time("1 soat 30 minut", NOW, TZ)
    assert got == NOW + timedelta(hours=1, minutes=30)


def test_half_hour():
    got = parse_uz_time("yarim soat", NOW, TZ)
    assert got == NOW + timedelta(minutes=30)


def test_structured_rel_minutes_wins():
    spec = TimeSpec(raw="biroz keyin", rel_minutes=45)
    assert parse_uz_time(spec, NOW, TZ) == NOW + timedelta(minutes=45)


def test_tomorrow_defaults_to_9_local():
    got = parse_uz_time("ertaga", NOW, TZ).astimezone(ZoneInfo(TZ))
    assert (got.hour, got.minute) == (9, 0)
    assert got.date() == (NOW.astimezone(ZoneInfo(TZ)).date() + timedelta(days=1))


def test_day_word_without_clock_asks_when_require_clock():
    # Reminders/messages/meetings must not silently invent 09:00 — they ask.
    for phrase in ("ertaga", "bugun", "indinga"):
        with pytest.raises(AmbiguousTime):
            parse_uz_time(phrase, NOW, TZ, require_clock=True)


def test_day_word_with_clock_is_fine_under_require_clock():
    got = parse_uz_time(
        "ertaga soat 15:00", NOW, TZ, require_clock=True
    ).astimezone(ZoneInfo(TZ))
    assert (got.hour, got.minute) == (15, 0)


def test_relative_offset_unaffected_by_require_clock():
    got = parse_uz_time("10 minutda", NOW, TZ, require_clock=True)
    assert got == NOW + timedelta(minutes=10)


def test_explicit_clock_24h_is_unambiguous():
    got = parse_uz_time("soat 21:00", NOW, TZ).astimezone(ZoneInfo(TZ))
    assert (got.hour, got.minute) == (21, 0)


def test_bare_hour_is_ambiguous():
    with pytest.raises(AmbiguousTime):
        parse_uz_time("9", NOW, TZ)


def test_empty_is_ambiguous():
    with pytest.raises(AmbiguousTime):
        parse_uz_time("", NOW, TZ)
