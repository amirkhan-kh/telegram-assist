"""Tests for `_parse_clock_text` — the pending-time clock answer parser.

Regression: a spoken/typed time with the Uzbek locative ("13:00 da", "soat 13 da")
used to fail and reply "Vaqtni tushunolmadim", while non-time noise must still be
rejected so an unrelated message never resolves a pending time slot.
"""

from __future__ import annotations

from app.services.dispatcher import _parse_clock_text


def test_parse_clock_basic_forms():
    assert _parse_clock_text("22:00") == (22, 0)
    assert _parse_clock_text("9:30") == (9, 30)
    assert _parse_clock_text("soat 9") == (9, 0)
    assert _parse_clock_text("9.30") == (9, 30)


def test_parse_clock_uzbek_suffix_forms():
    assert _parse_clock_text("13:00 da") == (13, 0)
    assert _parse_clock_text("soat 13 da") == (13, 0)
    assert _parse_clock_text("13da") == (13, 0)
    assert _parse_clock_text("13:00da") == (13, 0)
    assert _parse_clock_text("9 larda") == (9, 0)


def test_parse_clock_rejects_non_time():
    assert _parse_clock_text("Ona❤️") is None
    assert _parse_clock_text("salom") is None
    assert _parse_clock_text("25:00") is None  # hour out of range
    assert _parse_clock_text("") is None
