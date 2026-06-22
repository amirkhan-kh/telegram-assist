"""All ORM models. Importing this package registers every table on
``Base.metadata`` (used by Alembic autogenerate and create_all)."""

from __future__ import annotations

from app.db.base import Base
from app.db.models.channel import Channel, ChannelPost, Digest
from app.db.models.decision import Decision
from app.db.models.document import DocumentPhoto
from app.db.models.enums import (
    DebtDirection,
    DebtStatus,
    EventCategory,
    EventStatus,
    MeetingAlertKind,
    MeetingStatus,
    MessageStatus,
    NotifyTargetKind,
    Priority,
    ReminderStatus,
    ScheduleKind,
    SendMode,
    Source,
    TaskKind,
    TaskStatus,
)
from app.db.models.event import Event
from app.db.models.finance import DebtRecord
from app.db.models.meeting import Meeting, MeetingAlert
from app.db.models.message import ScheduledMessage
from app.db.models.person import Person
from app.db.models.reminder import Reminder
from app.db.models.setting import Secret, Setting
from app.db.models.task import Task

__all__ = [
    "Base",
    # models
    "Person",
    "Reminder",
    "Task",
    "DebtRecord",
    "Meeting",
    "MeetingAlert",
    "Channel",
    "ChannelPost",
    "Digest",
    "ScheduledMessage",
    "Event",
    "Decision",
    "DocumentPhoto",
    "Setting",
    "Secret",
    # enums
    "EventCategory",
    "EventStatus",
    "SendMode",
    "Source",
    "ReminderStatus",
    "TaskKind",
    "TaskStatus",
    "Priority",
    "DebtDirection",
    "DebtStatus",
    "MeetingStatus",
    "MeetingAlertKind",
    "NotifyTargetKind",
    "MessageStatus",
    "ScheduleKind",
]
