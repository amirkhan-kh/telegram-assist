"""Structured-output schema for the Gemini NLU router.

Instead of forced function calling, the Gemini router makes a single
``generate_content`` call with ``response_schema=NLUResult`` and
``response_mime_type="application/json"``. Gemini then constrains its decoding
to this exact shape and the SDK hands back one validated :class:`NLUResult`.

``NLUResult`` is an *envelope*: a ``reasoning`` scratch field (filled first so
the model commits to a perspective before choosing), the chosen ``intent``
name, and one optional sub-object per intent. The model fills exactly the
sub-object that matches ``intent`` with that intent's parameters — reusing the
very same pydantic models the dispatcher already validates against
(:mod:`app.brain.intents`), so nothing downstream changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.brain.intents import (
    AddFinance,
    AddImportantDate,
    AnalyzeContacts,
    AnswerQuestion,
    AssignTaskWithFollowup,
    CancelItem,
    CreatePromise,
    CreateReminder,
    FindFreeSlots,
    GetChatMessages,
    GetDigest,
    GetNews,
    GetWeather,
    JarvisBriefing,
    ListAgenda,
    ListContacts,
    ListDecisions,
    ListEmails,
    ListFinance,
    ListImportantDates,
    ListMeetings,
    ListReminders,
    LogDecision,
    SaveToNotion,
    ScheduleMeeting,
    ScheduleMessage,
    SearchChatMedia,
    SearchTelegramArchive,
    SendMessage,
    ShowCalendar,
    SummarizeChat,
)

# The intent name the model must choose. Keep these strings identical to the
# field names below and to ``INTENT_MODELS`` in :mod:`app.brain.intent_router`.
IntentName = Literal[
    "send_message",
    "schedule_message",
    "create_reminder",
    "create_promise",
    "assign_task_with_followup",
    "schedule_meeting",
    "find_free_slots",
    "add_finance",
    "get_digest",
    "cancel_item",
    "list_contacts",
    "analyze_contacts",
    "list_finance",
    "list_agenda",
    "list_meetings",
    "add_important_date",
    "list_important_dates",
    "log_decision",
    "list_decisions",
    "list_reminders",
    "list_emails",
    "save_to_notion",
    "show_calendar",
    "get_weather",
    "get_news",
    "jarvis_briefing",
    "get_chat_messages",
    "search_chat_media",
    "summarize_chat",
    "search_telegram_archive",
    "answer_question",
    "unknown",
]


class NLUResult(BaseModel):
    """One validated NLU decision: the chosen intent plus its parameters.

    The model sets ``intent`` and fills ONLY the matching sub-object. Every
    other sub-object stays ``None``. ``unknown`` means no intent fits — leave
    all sub-objects empty.
    """

    reasoning: str = Field(
        description=(
            "One short sentence (English): who acts and which single intent "
            "fits. Decide this BEFORE setting `intent`."
        )
    )
    intent: IntentName

    send_message: SendMessage | None = None
    schedule_message: ScheduleMessage | None = None
    create_reminder: CreateReminder | None = None
    create_promise: CreatePromise | None = None
    assign_task_with_followup: AssignTaskWithFollowup | None = None
    schedule_meeting: ScheduleMeeting | None = None
    find_free_slots: FindFreeSlots | None = None
    add_finance: AddFinance | None = None
    get_digest: GetDigest | None = None
    cancel_item: CancelItem | None = None
    list_contacts: ListContacts | None = None
    analyze_contacts: AnalyzeContacts | None = None
    list_finance: ListFinance | None = None
    list_agenda: ListAgenda | None = None
    list_meetings: ListMeetings | None = None
    add_important_date: AddImportantDate | None = None
    list_important_dates: ListImportantDates | None = None
    log_decision: LogDecision | None = None
    list_decisions: ListDecisions | None = None
    list_reminders: ListReminders | None = None
    list_emails: ListEmails | None = None
    save_to_notion: SaveToNotion | None = None
    show_calendar: ShowCalendar | None = None
    get_weather: GetWeather | None = None
    get_news: GetNews | None = None
    jarvis_briefing: JarvisBriefing | None = None
    get_chat_messages: GetChatMessages | None = None
    search_chat_media: SearchChatMedia | None = None
    summarize_chat: SummarizeChat | None = None
    search_telegram_archive: SearchTelegramArchive | None = None
    answer_question: AnswerQuestion | None = None


class NLUMultiResult(BaseModel):
    """One or more ordered actions parsed from a single owner message.

    A message may bundle several commands ("... ayt, hozir ogohlantir va bir
    minutdan keyin yana ogohlantir"). Each becomes one :class:`NLUResult` in
    ``actions``, in the order the owner said them; a single-command message
    yields exactly one element. Typically 1–5 actions.
    """

    actions: list[NLUResult] = Field(
        description=(
            "Ordered list, one NLUResult per distinct command the owner gave "
            "(usually 1, up to ~5). Preserve the spoken order."
        )
    )
