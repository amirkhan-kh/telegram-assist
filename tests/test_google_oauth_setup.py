"""Tests for the OAuth setup path: re-auth handling + .env auto-update."""

from __future__ import annotations

from datetime import UTC, datetime

from app.brain.intent_router import RoutedIntent
from app.brain.intents import FindFreeSlots, ScheduleMeeting, TimeSpec
from app.services.dispatcher import _is_google_auth_error, dispatch
from scripts.google_auth import _update_env


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


class _AuthErrorCalendar:
    """A calendar service whose every call fails like an expired token."""

    def available(self) -> bool:
        return True

    async def find_free_slots(self, **_kw):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")

    async def create_event_with_meet(self, **_kw):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")


# ── error classification ──────────────────────────────────────────────────────
def test_is_google_auth_error_detects_token_problems():
    assert _is_google_auth_error(RuntimeError("invalid_grant: expired"))
    assert _is_google_auth_error(type("RefreshError", (Exception,), {})("x"))
    assert _is_google_auth_error(Exception("HttpError 401: Unauthorized"))
    assert not _is_google_auth_error(ValueError("an unrelated failure"))


# ── dispatcher degrades gracefully on an expired token ────────────────────────
async def test_find_free_slots_reauth_message(registry):
    registry.calendar_service = _AuthErrorCalendar()
    routed = RoutedIntent(
        "find_free_slots",
        FindFreeSlots(date_range=TimeSpec(raw="ertaga"), duration_minutes=30),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "python -m scripts.google_auth" in result.text


async def test_schedule_meeting_still_created_with_reauth_note(registry):
    registry.calendar_service = _AuthErrorCalendar()
    routed = RoutedIntent(
        "schedule_meeting",
        ScheduleMeeting(
            title="Sinov",
            when=TimeSpec(raw="ertaga soat 10"),
            duration_minutes=30,
            invitee_names=[],
            create_meet_link=True,
            notify_target_name=None,
        ),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    # The meeting is still scheduled; only the Meet link is skipped with a note.
    assert "Uchrashuv rejalashtirildi" in result.text
    assert "Google ruxsati tugagan" in result.text


# ── .env auto-update ──────────────────────────────────────────────────────────
def test_update_env_updates_existing_and_appends_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=1\nGOOGLE_CLIENT_ID=old\nBAR=2\n")
    ok = _update_env(
        {"GOOGLE_CLIENT_ID": "new", "GOOGLE_OAUTH_REFRESH_TOKEN": "tok"},
        env_path=str(env),
    )
    assert ok
    text = env.read_text()
    assert "GOOGLE_CLIENT_ID=new" in text
    assert "GOOGLE_CLIENT_ID=old" not in text          # replaced, not duplicated
    assert "GOOGLE_OAUTH_REFRESH_TOKEN=tok" in text     # appended
    assert "FOO=1" in text and "BAR=2" in text          # untouched


def test_update_env_missing_file_returns_false(tmp_path):
    assert _update_env({"X": "y"}, env_path=str(tmp_path / "absent.env")) is False
