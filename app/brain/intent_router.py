"""Intent router — the single Anthropic call that turns text into an intent.

:class:`IntentRouter` performs exactly one ``messages.create`` request with
forced tool use, then validates the chosen tool's input against the matching
pydantic model in :mod:`app.brain.intents`. The result is a :class:`RoutedIntent`
the dispatcher can act on. No side effects beyond that one network request.

When no Anthropic API key is configured the router is constructed with a ``None``
client; :meth:`route` then raises a clear, Uzbek-friendly :class:`RuntimeError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.brain.intents import (
    AddFinance,
    AddImportantDate,
    AnalyzeActivity,
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
from app.brain.prompts import ROUTER_SYSTEM
from app.brain.tools import ANTHROPIC_TOOLS
from app.config import get_settings
from app.integrations.anthropic_client import get_async_anthropic
from app.logging_conf import get_logger

logger = get_logger(__name__)


# Maps each tool name to the pydantic model used to validate its input.
INTENT_MODELS: dict[str, type[BaseModel]] = {
    "send_message": SendMessage,
    "schedule_message": ScheduleMessage,
    "create_reminder": CreateReminder,
    "create_promise": CreatePromise,
    "assign_task_with_followup": AssignTaskWithFollowup,
    "schedule_meeting": ScheduleMeeting,
    "find_free_slots": FindFreeSlots,
    "add_finance": AddFinance,
    "get_digest": GetDigest,
    "cancel_item": CancelItem,
    "list_contacts": ListContacts,
    "analyze_contacts": AnalyzeContacts,
    "analyze_activity": AnalyzeActivity,
    "list_finance": ListFinance,
    "list_agenda": ListAgenda,
    "list_meetings": ListMeetings,
    "add_important_date": AddImportantDate,
    "list_important_dates": ListImportantDates,
    "log_decision": LogDecision,
    "list_decisions": ListDecisions,
    "list_reminders": ListReminders,
    "list_emails": ListEmails,
    "save_to_notion": SaveToNotion,
    "show_calendar": ShowCalendar,
    "get_weather": GetWeather,
    "get_news": GetNews,
    "jarvis_briefing": JarvisBriefing,
    "get_chat_messages": GetChatMessages,
    "search_chat_media": SearchChatMedia,
    "summarize_chat": SummarizeChat,
    "search_telegram_archive": SearchTelegramArchive,
    "answer_question": AnswerQuestion,
}


@dataclass
class RoutedIntent:
    """A validated intent ready for dispatch.

    ``name`` is the tool/intent name (or "unknown"); ``params`` is the validated
    pydantic model instance (or ``None``); ``raw_input`` is the model's raw tool
    input for debugging/logging.
    """

    name: str
    params: BaseModel | None
    raw_input: dict[str, Any]


class IntentRouter:
    """Wraps the Anthropic client and routes utterances to intents."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        settings = get_settings()
        # Build the client lazily; may legitimately be None when no key is set.
        self.client = client if client is not None else get_async_anthropic(settings)
        self.model = model or settings.anthropic_model

    async def route(self, utterance: str, *, now_iso: str) -> RoutedIntent:
        """Route ``utterance`` to a validated :class:`RoutedIntent`.

        Args:
            utterance: The owner's free-form (Uzbek) text.
            now_iso: Current time as an ISO-8601 string, used by the model to
                interpret relative phrases.

        Raises:
            RuntimeError: when no Anthropic client is configured.
        """

        if self.client is None:
            raise RuntimeError(
                "Sun'iy intellekt sozlanmagan: ANTHROPIC_API_KEY topilmadi. "
                "Iltimos, kalitni .env faylga qo'shing."
            )

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": ROUTER_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            # Reuse the module-level tool list (built once) instead of rebuilding
            # all 29 schemas per message; the Anthropic SDK never mutates it.
            tools=ANTHROPIC_TOOLS,
            tool_choice={"type": "any"},
            messages=[
                {
                    "role": "user",
                    "content": f"<now>{now_iso}</now> {utterance}",
                }
            ],
        )

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = block.name
            raw_input: dict[str, Any] = dict(block.input or {})
            model_cls = INTENT_MODELS.get(name)
            if model_cls is None:
                logger.warning("router.unknown_tool", tool=name)
                return RoutedIntent("unknown", None, raw_input)
            try:
                params = model_cls.model_validate(raw_input)
            except Exception:  # noqa: BLE001 — surface as "unknown", never crash
                logger.exception("router.validation_failed", tool=name, raw=raw_input)
                return RoutedIntent("unknown", None, raw_input)
            logger.info("router.routed", tool=name)
            return RoutedIntent(name, params, raw_input)

        logger.info("router.no_tool_use")
        return RoutedIntent("unknown", None, {})

    async def route_many(self, utterance: str, *, now_iso: str) -> list[RoutedIntent]:
        """Route one utterance to ordered intents.

        The Anthropic router runs with forced single tool use, so multi-action
        splitting is only available on the Gemini provider; here we return the
        single best intent (callers handle the single-element list uniformly).
        """
        return [await self.route(utterance, now_iso=now_iso)]
