"""Pydantic v2 intent models — the validated shape of every routed command.

Each model mirrors exactly one Anthropic tool (see :mod:`app.brain.tools`) and
one dispatcher branch (see :mod:`app.services.dispatcher`). The router validates
the tool's JSON input against the matching model here before anything acts on it.

``TimeSpec`` carries the *verbatim* time phrase the owner used (e.g. "5 minutda",
"ertaga soat 9"). The brain never computes absolute timestamps itself — that is
the job of :func:`app.brain.time_parse.parse_uz_time`, which runs later with a
concrete ``now``.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, Field


class DeliveryMode(str, enum.Enum):
    """How an outbound message should reach its recipient.

    ``ask`` means the owner did not specify a channel, so the assistant shows
    «🎙 Ovozli | 📝 Matn» buttons and lets them pick before sending.
    """

    text = "text"
    voice = "voice"
    both = "both"
    ask = "ask"


class Formality(str, enum.Enum):
    """Register the outbound message should be written in.

    ``neutral`` is the everyday, polite tone the brain uses by default.
    ``formal`` is the respectful, official register the owner asks for with
    words like "rasmiy"/"rasmiyroq" — siz-address, complete sentences, no slang.
    """

    neutral = "neutral"
    formal = "formal"


class TimeSpec(BaseModel):
    """A time expression, captured verbatim plus light structured hints.

    ``raw`` is always the owner's original phrase. ``rel_minutes`` is filled
    only when the model is confident about a relative offset; otherwise the
    Uzbek regex parser interprets ``raw``.
    """

    raw: str = Field(description="The time phrase exactly as the owner said it.")
    kind: Literal["relative", "absolute", "none"] = "relative"
    rel_minutes: int | None = Field(
        default=None, description="Relative offset in minutes, if clearly stated."
    )
    clock_hint: str | None = Field(
        default=None, description="A clock time like '09:00' if present."
    )


class SendMessage(BaseModel):
    """Send a message to someone right now."""

    recipient_name: str
    content: str
    delivery: DeliveryMode = DeliveryMode.ask
    formality: Formality = Formality.neutral


class ScheduleMessage(BaseModel):
    """Send a message to someone at a later time."""

    recipient_name: str
    content: str
    when: TimeSpec
    delivery: DeliveryMode = DeliveryMode.ask
    formality: Formality = Formality.neutral


class RecurrenceSpec(BaseModel):
    """A repeating schedule for a recurring reminder.

    ``freq="none"`` (the default) means a one-shot reminder. For ``weekly`` set
    ``weekday`` (0=Monday … 6=Sunday). For ``monthly`` set ``day_of_month`` (1-31)
    or ``month_end=True`` for the last day of the month ("oy oxirida"). ``hour``/
    ``minute`` are the local clock time the reminder fires on each occurrence.
    """

    freq: Literal["none", "daily", "weekly", "monthly"] = "none"
    weekday: int | None = Field(default=None, description="0=Mon..6=Sun (weekly).")
    day_of_month: int | None = Field(default=None, description="1-31 (monthly).")
    month_end: bool = Field(default=False, description="Last day of month (monthly).")
    hour: int = 9
    minute: int = 0


class CreateReminder(BaseModel):
    """Remind the owner about something — once, or on a repeating schedule."""

    text: str
    when: TimeSpec
    pre_alerts_minutes: list[int] = Field(default_factory=lambda: [15])
    recurrence: RecurrenceSpec | None = None


class CreatePromise(BaseModel):
    """The owner promised to do something themselves by a deadline."""

    what: str
    deadline: TimeSpec
    counterparty_name: str | None = None
    pre_alerts_minutes: list[int] = Field(default_factory=lambda: [30, 10])


class AssignTaskWithFollowup(BaseModel):
    """Someone else owes the owner a task; track it and follow up."""

    assignee_name: str
    task: str
    deadline: TimeSpec
    pre_alert_to_owner_minutes: list[int] = Field(default_factory=lambda: [15])
    auto_followup_to_assignee: bool = True
    followup_offsets_minutes: list[int] = Field(default_factory=lambda: [-15, 0])


class ScheduleMeeting(BaseModel):
    """Schedule a meeting (optionally with a Meet link)."""

    title: str
    when: TimeSpec
    duration_minutes: int = 30
    invitee_names: list[str] = Field(default_factory=list)
    create_meet_link: bool = True
    notify_target_name: str | None = None


class FindFreeSlots(BaseModel):
    """Find free time slots in the owner's calendar."""

    date_range: TimeSpec
    duration_minutes: int = 30


class AddFinance(BaseModel):
    """Record a debt (owner owes) or credit (someone owes the owner)."""

    direction: Literal["debt", "credit"]
    counterparty_name: str
    amount: float
    currency: str = "UZS"
    due: TimeSpec | None = None
    note: str | None = None


class GetDigest(BaseModel):
    """Produce a digest of recent channel activity."""

    top_n: int = 5


class CancelItem(BaseModel):
    """Cancel a previously created item."""

    item_kind: Literal["reminder", "promise", "followup", "meeting", "message"]
    selector: str


class ListContacts(BaseModel):
    """List the owner's saved contacts (optionally filtered by a name fragment)."""

    query: str | None = None
    limit: int = 40


class ListFinance(BaseModel):
    """List outstanding debts/credits with a computed total per currency.

    ``they_owe_me`` = people who owe the owner; ``i_owe_them`` = whom the owner
    owes; ``all`` = both.
    """

    direction: Literal["they_owe_me", "i_owe_them", "all"] = "all"


class ListAgenda(BaseModel):
    """List the owner's current plan: reminders, promises, tracked tasks, meetings."""

    scope: Literal["today", "all"] = "all"


class ListMeetings(BaseModel):
    """List the owner's scheduled meetings (with Meet links)."""

    scope: Literal["today", "all"] = "all"


class AddImportantDate(BaseModel):
    """Save an important date / birthday with day-before reminders.

    ``month``/``day`` are the calendar date (e.g. 5-avgust -> month=8, day=5).
    ``yearly`` defaults to True (birthdays, annual renewals); set ``year`` for a
    one-off date. ``remind_days_before`` lists how many days ahead to alert.
    """

    title: str
    category: Literal[
        "birthday", "document", "payment", "travel", "health", "other"
    ] = "other"
    month: int = Field(description="Month 1-12.")
    day: int = Field(description="Day of month 1-31.")
    year: int | None = None
    yearly: bool = True
    remind_days_before: list[int] = Field(default_factory=lambda: [1])


class ListImportantDates(BaseModel):
    """List the owner's saved important dates / birthdays."""

    days: int = Field(default=365, description="Look-ahead window in days.")


class LogDecision(BaseModel):
    """Record a personal decision in the owner's decisions journal."""

    text: str
    tag: str | None = None


class ListDecisions(BaseModel):
    """List the owner's recent journalled decisions."""

    limit: int = 20


class ListReminders(BaseModel):
    """List the owner's active reminders (one-shot upcoming + recurring)."""

    limit: int = 50


class ListEmails(BaseModel):
    """Show the owner's recent important / unread Gmail messages."""

    limit: int = 5


class ShowCalendar(BaseModel):
    """Show the owner's Google Calendar events (today or this week)."""

    scope: Literal["today", "week"] = "week"


class SaveToNotion(BaseModel):
    """Save a free-form note / plan to the owner's Notion workspace."""

    text: str
    title: str | None = None
