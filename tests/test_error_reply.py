"""Tests for the owner-facing NLU error messages."""

from __future__ import annotations

from app.bot.handlers import (
    _ERR_AUTH,
    _ERR_NO_CREDIT,
    _ERR_RATE,
    _GENERIC_ERROR,
    _error_reply,
)


class _Named(Exception):
    """Helper to fake a named exception class (e.g. RateLimitError)."""


def test_no_credit_message():
    exc = Exception(
        "Error code: 400 - Your credit balance is too low to access the "
        "Anthropic API. Please go to Plans & Billing."
    )
    assert _error_reply(exc) == _ERR_NO_CREDIT


def test_auth_message():
    exc = Exception("Error code: 401 - invalid x-api-key")
    assert _error_reply(exc) == _ERR_AUTH


def test_rate_limit_by_class_name():
    rate_err = type("RateLimitError", (_Named,), {})("429")
    assert _error_reply(rate_err) == _ERR_RATE


def test_gemini_quota_maps_to_rate_not_anthropic_credit():
    # Gemini's 429 message mentions "billing" but is a quota issue, NOT an
    # Anthropic credit problem — it must map to the rate message.
    exc = Exception(
        "429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check "
        "your plan and billing details. Quota exceeded for metric: "
        "generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash"
    )
    assert _error_reply(exc) == _ERR_RATE


def test_unknown_error_is_generic():
    assert _error_reply(ValueError("something odd")) == _GENERIC_ERROR
