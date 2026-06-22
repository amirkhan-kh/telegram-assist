"""Enumerations shared across models, services, brain and scheduler.

These string enums are the cross-module contract — handlers, repositories,
the NLU layer and the scheduler all reference the same names/values.
"""

from __future__ import annotations

import enum


class SendMode(str, enum.Enum):
    text = "text"
    voice = "voice"
    both = "both"


class Source(str, enum.Enum):
    manual = "manual"
    bot = "bot"
    voice = "voice"
    nlu = "nlu"


class ReminderStatus(str, enum.Enum):
    pending = "pending"
    fired = "fired"
    done = "done"
    cancelled = "cancelled"


class TaskKind(str, enum.Enum):
    self_promise = "self_promise"   # the OWNER promised to do something
    delegated = "delegated"         # someone owes the OWNER (dependent follow-up)
    generic = "generic"


class TaskStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    overdue = "overdue"
    cancelled = "cancelled"


class Priority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"


class DebtDirection(str, enum.Enum):
    they_owe_me = "they_owe_me"
    i_owe_them = "i_owe_them"


class DebtStatus(str, enum.Enum):
    open = "open"
    partially_paid = "partially_paid"
    settled = "settled"


class MeetingStatus(str, enum.Enum):
    scheduled = "scheduled"
    started = "started"
    done = "done"
    cancelled = "cancelled"


class NotifyTargetKind(str, enum.Enum):
    person = "person"
    group = "group"


class MeetingAlertKind(str, enum.Enum):
    reminder = "reminder"      # ping the owner (30/15 min before)
    send_link = "send_link"    # deliver the Meet link at start, via userbot


class MessageStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    cancelled = "cancelled"
    failed = "failed"


class EventCategory(str, enum.Enum):
    """Kind of important date (drives the icon shown to the owner)."""

    birthday = "birthday"     # tug'ilgan kun
    document = "document"     # pasport/guvohnoma/sug'urta muddati
    payment = "payment"       # to'lov sanasi
    travel = "travel"         # safar
    health = "health"         # doktor/tekshiruv
    other = "other"


class EventStatus(str, enum.Enum):
    active = "active"
    done = "done"
    cancelled = "cancelled"


class ScheduleKind(str, enum.Enum):
    """What a persisted APScheduler job dispatches to (see app.scheduler.jobs).

    The job is stored as ``app.scheduler.jobs:execute_job`` with
    ``args=[kind.value, row_id, role]``; ``execute_job`` routes on this.
    """

    reminder = "reminder"                  # fire a Reminder row -> owner
    promise_alert = "promise_alert"        # self-promise pre-alert/deadline -> owner
    followup_owner = "followup_owner"      # delegated task: alert the owner
    followup_assignee = "followup_assignee"  # delegated task: nudge the assignee (userbot)
    scheduled_message = "scheduled_message"  # deferred outbound message (userbot)
    meeting_alert = "meeting_alert"        # 30/15 reminder -> owner
    meeting_link = "meeting_link"          # deliver Meet link at start -> target
    debt_reminder = "debt_reminder"        # finance nudge
    digest = "digest"                      # build + deliver channel digest
    important_date = "important_date"      # birthday/important-date pre-alert -> owner
    morning_briefing = "morning_briefing"  # daily 07:00 plan -> owner
    evening_review = "evening_review"       # nightly day-end review -> owner
