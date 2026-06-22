"""Safety guards for sending as the owner's *user account*.

Sending from a real account too fast (or too much) is the quickest way to get
flagged or banned. Two guards protect the userbot:

  * :class:`RateLimiter` — enforces a global minimum interval between sends and a
    per-day cap (raising :class:`DailyLimitReached` past the cap).
  * :func:`RateLimiter.with_flood_retry` — transparently retries a Telethon call
    that hits ``FloodWaitError``, sleeping the requested seconds (plus a small
    deterministic jitter) before each retry.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telethon.errors import FloodWaitError

from app.logging_conf import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class DailyLimitReached(Exception):
    """Raised when the per-day send budget for the userbot is exhausted."""


class RateLimiter:
    """Global send pacing + daily quota for the userbot.

    The limiter is intentionally *global* (not per-peer): Telegram's flood
    heuristics watch the account as a whole, so spacing every outbound send is
    the safe default. An :class:`asyncio.Lock` serializes the spacing check so
    concurrent senders cannot bypass the interval.
    """

    def __init__(self, min_interval: float, daily_limit: int) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.daily_limit = int(daily_limit)
        self._lock = asyncio.Lock()
        self._last_send_monotonic: float | None = None
        self._day: dt.date | None = None
        self._count_today = 0

    def _roll_day_if_needed(self) -> None:
        """Reset the daily counter when the UTC calendar day changes."""
        today = dt.datetime.now(dt.UTC).date()
        if self._day != today:
            self._day = today
            self._count_today = 0

    async def acquire(self, peer: Any) -> None:
        """Block until it is safe to send, then account for this send.

        Enforces the minimum inter-send interval and the daily limit. ``peer``
        is accepted for symmetry/observability but pacing is global.
        """
        async with self._lock:
            self._roll_day_if_needed()
            if self.daily_limit > 0 and self._count_today >= self.daily_limit:
                logger.warning(
                    "userbot.daily_limit_reached",
                    limit=self.daily_limit,
                    peer=str(peer),
                )
                raise DailyLimitReached(
                    f"Userbot daily send limit reached ({self.daily_limit})."
                )

            now = time.monotonic()
            if self._last_send_monotonic is not None and self.min_interval > 0:
                elapsed = now - self._last_send_monotonic
                wait = self.min_interval - elapsed
                if wait > 0:
                    logger.debug("userbot.rate_limit.sleep", seconds=round(wait, 3))
                    await asyncio.sleep(wait)
                    now = time.monotonic()

            self._last_send_monotonic = now
            self._count_today += 1

    @staticmethod
    async def with_flood_retry(
        coro_factory: Callable[[], Awaitable[T]],
        *,
        max_retries: int = 3,
    ) -> T:
        """Await ``coro_factory()``; retry on ``FloodWaitError``.

        ``coro_factory`` must be a zero-arg callable returning a fresh awaitable
        each time (an awaitable cannot be awaited twice). On ``FloodWaitError``
        we sleep the server-requested seconds plus a small deterministic jitter
        (derived from the attempt index — no randomness) and retry up to
        ``max_retries`` times, then re-raise.
        """
        attempt = 0
        while True:
            try:
                return await coro_factory()
            except FloodWaitError as exc:
                attempt += 1
                if attempt > max_retries:
                    logger.error(
                        "userbot.flood_wait.giving_up",
                        seconds=exc.seconds,
                        attempts=attempt,
                    )
                    raise
                jitter = 0.5 * attempt
                delay = float(exc.seconds) + jitter
                logger.warning(
                    "userbot.flood_wait.retry",
                    seconds=exc.seconds,
                    sleep=round(delay, 3),
                    attempt=attempt,
                )
                await asyncio.sleep(delay)
