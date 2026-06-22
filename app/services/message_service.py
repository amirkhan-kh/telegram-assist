"""MessageService — deferred outbound messages (text/voice) sent via the userbot.

``schedule_message`` persists a ScheduledMessage and registers a
``scheduled_message`` job for ``send_at``. ``send_now`` resolves the target peer,
honours ``test_mode`` (third-party messages are redirected to the owner), sends
via ``registry.sender``, and marks the row sent/failed.

Scheduling goes through ``app.scheduler.jobs`` (imported lazily).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.enums import MessageStatus, ScheduleKind, SendMode, Source
from app.db.models.message import ScheduledMessage
from app.logging_conf import get_logger
from app.repositories import message_repo, person_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class MessageService:
    """Schedule and deliver outbound messages via the userbot."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def schedule_message(
        self,
        *,
        recipient_id: int | None = None,
        chat_id: int | None = None,
        content: str,
        delivery: SendMode = SendMode.voice,
        send_at: datetime,
        source: Source = Source.nlu,
    ) -> ScheduledMessage:
        """Create a scheduled message row and register its delivery job."""
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            message = await message_repo.create(
                session,
                recipient_id=recipient_id,
                chat_id=chat_id,
                content=content,
                delivery=delivery,
                send_at=send_at,
                status=MessageStatus.pending,
                source=source,
            )
            mid = message.id

        job_id = schedule_at(
            self.registry.scheduler,
            kind=ScheduleKind.scheduled_message,
            row_id=mid,
            run_at=send_at,
        )
        async with self.registry.session() as session:
            await message_repo.set_job_id(session, mid, job_id)

        logger.info("message.scheduled", message_id=mid, send_at=send_at.isoformat())
        async with self.registry.session() as session:
            return await message_repo.get(session, mid)  # type: ignore[return-value]

    async def send_message_now(
        self,
        *,
        recipient_id: int | None = None,
        chat_id: int | None = None,
        content: str,
        delivery: SendMode = SendMode.voice,
        source: Source = Source.nlu,
    ) -> int:
        """Create a message row and deliver it immediately — NO scheduler job.

        Immediate sends must not register a ``scheduled_message`` job: the job
        (run_at=now) would fire while ``send_now`` is still awaiting the userbot
        (the rate limiter adds seconds), and both would read ``pending`` and
        deliver — a duplicate. This path skips the job entirely.
        """
        async with self.registry.session() as session:
            message = await message_repo.create(
                session,
                recipient_id=recipient_id,
                chat_id=chat_id,
                content=content,
                delivery=delivery,
                send_at=utcnow(),
                status=MessageStatus.pending,
                source=source,
            )
            mid = message.id
        await self.send_now(mid)
        return mid

    async def cancel(self, message_id: int) -> bool:
        """Cancel a pending scheduled message and drop its delivery job.

        Returns ``True`` if a pending message was cancelled, ``False`` if it was
        missing or already sent/cancelled.
        """
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            message = await message_repo.get(session, message_id)
            if message is None or message.status != MessageStatus.pending:
                return False
            job_id = message.apscheduler_job_id
            await message_repo.mark_cancelled(session, message_id)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("message.cancelled", message_id=message_id)
        return True

    async def send_now(self, message_id: int) -> None:
        """Resolve the peer and deliver the message; mark it sent or failed."""
        async with self.registry.session() as session:
            message = await message_repo.get(session, message_id)
            if message is None:
                logger.warning("message.send.missing", message_id=message_id)
                return
            if message.status != MessageStatus.pending:
                return
            content = message.content
            delivery = message.delivery
            chat = message.chat_id
            recipient_id = message.recipient_id
            recipient_name = "kishi"
            if chat is None and recipient_id is not None:
                person = await person_repo.get_by_id(session, recipient_id)
                if person is not None:
                    chat = person.telegram_user_id
                    recipient_name = person.display_name

        settings = self.registry.settings
        owner_chat_id = settings.owner_chat_id
        is_to_owner = chat is not None and chat == owner_chat_id

        try:
            if settings.test_mode and not is_to_owner:
                notifier = self.registry.notification_service
                text = f"[TEST -> {recipient_name}]\n{content}"
                if notifier is not None:
                    await notifier.notify_owner(text)
                    # Preview the real voice note to the owner so the cloned
                    # voice can be verified before enabling real sends. Only
                    # when voice actually works (else the text above suffices).
                    voice = self.registry.voice_service
                    if (
                        delivery in (SendMode.voice, SendMode.both)
                        and voice is not None
                        and voice.available()
                    ):
                        await notifier.notify_owner_voice(content)
            else:
                sender = self.registry.sender
                if sender is None or chat is None:
                    raise RuntimeError("No sender or unresolved recipient")
                await sender.send(chat, content, delivery)
        except Exception as exc:  # noqa: BLE001 - record failure, never raise out
            logger.warning(
                "message.send.failed", message_id=message_id, error=str(exc)
            )
            async with self.registry.session() as session:
                await message_repo.mark_failed(session, message_id)
            return

        async with self.registry.session() as session:
            await message_repo.mark_sent(session, message_id)
        logger.info("message.sent", message_id=message_id)
