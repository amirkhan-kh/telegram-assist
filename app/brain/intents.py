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
    """Send a message to someone at a later time.

    ``meeting_notice`` marks a message that NOTIFIES the recipient about a
    meeting/appointment scheduled for ``when``: it is delivered immediately AND
    again at ``when`` (a heads-up now plus a reminder at the time). For an online
    Meet (the owner said "meet"/"meeting"/"miting"), ``create_meet_link`` asks
    the assistant to mint a Google Meet link and weave it into the message.
    """

    recipient_name: str
    content: str
    when: TimeSpec
    delivery: DeliveryMode = DeliveryMode.ask
    formality: Formality = Formality.neutral
    meeting_notice: bool = False
    create_meet_link: bool = False


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


class AnalyzeContacts(BaseModel):
    """An analytical / open question about the owner's WHOLE contact list.

    Covers duplicates, counts, groupings, statistics, or "all contacts named X" —
    anything that reasons OVER the address book rather than looking up one person
    to message. Examples: "eng ko'p bir xil ismli kontaktlar qaysi", "bir xil
    ismli kontaktlar nechta, ro'yxatini tuz", "Ali ismli barcha kontaktlarim",
    "usernameyi yo'q kontaktlar nechta". NOT a simple lookup to message someone
    (that stays list_contacts)."""

    query: str = Field(
        description="The owner's full contacts question/task, copied verbatim."
    )


class AnalyzeActivity(BaseModel):
    """An analytical / open question over the owner's personal-productivity data —
    reminders, tasks/promises, meetings, debts, important dates and decisions.

    Examples: "shu oyda nechta uchrashuvim bor", "eng katta qarzim kimda",
    "bajarilmagan vazifalarim qaysi", "bu hafta nimalar ko'p", "rejalarimni
    umumiy tahlil qil". NOT a plain single-domain list
    (list_agenda / list_reminders / list_meetings / list_finance …)."""

    query: str = Field(
        description="The owner's full planning/finance question, copied verbatim."
    )


class AnalyzeChats(BaseModel):
    """An analytical / open question ACROSS the owner's Telegram conversations —
    who they message most, most active chats, activity over time, incoming vs
    outgoing counts.

    Examples: "kim bilan ko'p yozishaman", "eng faol chatlarim qaysi", "oxirgi
    hafta kim menga ko'p yozdi", "eng ko'p kim menga yozgan". NOT reading or
    searching ONE specific chat (that is get_chat_messages / summarize_chat /
    search_telegram_archive)."""

    query: str = Field(
        description="The owner's full conversation-analytics question, verbatim."
    )


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


class GetWeather(BaseModel):
    """Show weather for a city/date range."""

    location: str | None = None
    scope: Literal["today", "tomorrow", "week"] = "today"


class JarvisBriefing(BaseModel):
    """Give the owner a compact Jarvis-style plan/weather briefing."""

    scope: Literal["today", "week"] = "today"


class GetNews(BaseModel):
    """Show the latest Uzbek-language world+local news headlines (titles linked)."""

    limit: int = 10


class SearchChatMedia(BaseModel):
    """Find and send media from a Telegram private chat."""

    contact_name: str
    media_type: Literal["photo", "video", "document", "any"] = "photo"
    direction: Literal["incoming", "outgoing", "both"] = "incoming"
    limit: int = 5


class GetChatMessages(BaseModel):
    """Show recent Telegram messages from a private chat."""

    contact_name: str
    direction: Literal["incoming", "outgoing", "both"] = "incoming"
    scope: Literal["recent", "today", "week"] = "recent"
    limit: int = 1


class SummarizeChat(BaseModel):
    """Summarize recent Telegram messages with a contact."""

    contact_name: str
    scope: Literal["recent", "today", "week"] = "recent"
    limit: int = 50


class AnswerQuestion(BaseModel):
    """Answer a general question or hold a natural conversation.

    The conversational/knowledge fallback: anything that is NOT one of the
    action intents above — general knowledge, facts, news, advice, opinions,
    definitions, translations, calculations, or plain chit-chat. The assistant
    replies in natural, voice-friendly Uzbek instead of falling back to
    "Tushunmadim". ``needs_fresh_info`` marks questions whose answer changes
    over time (news, prices, exchange rates, scores, "today/now" facts) so the
    answer can be grounded with a live web search; evergreen facts leave it
    ``False``.
    """

    query: str = Field(
        description="The owner's question or topic, cleaned of filler words."
    )
    needs_fresh_info: bool = Field(
        default=False,
        description=(
            "True when the answer depends on up-to-date/live info (news, prices, "
            "exchange rates, weather elsewhere, current events); else False."
        ),
    )


class SearchTelegramArchive(BaseModel):
    """Search all visible Telegram private chats, groups, and channels."""

    query: str = Field(
        description=(
            "What the owner is looking for: topic, visual description, voice "
            "meaning, or message meaning."
        )
    )
    chat_name: str | None = Field(
        default=None,
        description="Group/channel/private chat name if the owner named one.",
    )
    chat_types: Literal["all", "private", "groups", "channels"] = "all"
    media_type: Literal[
        "any", "text", "voice", "audio", "video", "photo", "document"
    ] = "any"
    scope: Literal["recent", "today", "week"] = "recent"
    limit: int = 3
