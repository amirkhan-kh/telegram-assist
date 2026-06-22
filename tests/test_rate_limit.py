"""Tests for rate-limit detection + countdown formatting helpers."""

from __future__ import annotations

from app.bot.handlers import (
    _fmt_dur,
    _is_daily_limit,
    _is_rate_limit,
    _retry_after_seconds,
)


class _Err(Exception):
    pass


def _exc(msg: str) -> _Err:
    return _Err(msg)


def test_is_rate_limit():
    assert _is_rate_limit(_exc("429 RESOURCE_EXHAUSTED: quota")) is True
    assert _is_rate_limit(_exc("Quota exceeded for ...")) is True
    assert _is_rate_limit(_exc("rate limit reached")) is True
    assert _is_rate_limit(_exc("connection reset")) is False


def test_is_daily_limit():
    daily = "429 RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProject ..."
    minute = "429 RESOURCE_EXHAUSTED ... GenerateRequestsPerMinutePerProject ..."
    assert _is_daily_limit(_exc(daily)) is True
    assert _is_daily_limit(_exc(minute)) is False


def test_retry_after_seconds():
    assert _retry_after_seconds(_exc("'retryDelay': '41s'")) == 41
    assert _retry_after_seconds(_exc("retryDelay: 7s")) == 7
    assert _retry_after_seconds(_exc("no delay mentioned")) is None


def test_fmt_dur():
    assert _fmt_dur(45) == "45 soniya"
    assert _fmt_dur(60) == "1:00"
    assert _fmt_dur(65) == "1:05"
    assert _fmt_dur(600) == "10:00"
