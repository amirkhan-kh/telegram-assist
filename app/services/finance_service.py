"""FinanceService — debts/credits (who owes the owner, whom the owner owes).

Adds entries (optionally with a due-date debt-reminder job), lists open entries,
settles them, and fires Uzbek owner notifications when a debt comes due.

Scheduling goes through ``app.scheduler.jobs`` (imported lazily).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from app.db.base import utcnow
from app.db.models.enums import DebtDirection, DebtStatus, ScheduleKind
from app.db.models.finance import DebtRecord
from app.logging_conf import get_logger
from app.repositories import finance_repo, person_repo
from app.services._timeutil import to_local_str

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class FinanceService:
    """Track debts and credits with optional due-date reminders."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def add_entry(
        self,
        *,
        counterparty_id: int,
        direction: DebtDirection,
        amount: float | Decimal,
        currency: str = "UZS",
        due_dt: datetime | None = None,
        description: str | None = None,
    ) -> DebtRecord:
        """Create a debt record; schedule a debt-reminder job if a due date is set."""
        from app.scheduler.jobs import schedule_at

        async with self.registry.session() as session:
            record = await finance_repo.create(
                session,
                counterparty_id=counterparty_id,
                direction=direction,
                amount=Decimal(str(amount)),
                currency=currency,
                description=description,
                incurred_at=utcnow(),
                due_at=due_dt,
                status=DebtStatus.open,
            )
            did = record.id

        if due_dt is not None and due_dt > utcnow():
            job_id = schedule_at(
                self.registry.scheduler,
                kind=ScheduleKind.debt_reminder,
                row_id=did,
                run_at=due_dt,
                role="deadline",
            )
            async with self.registry.session() as session:
                await finance_repo.set_job_id(session, did, job_id)

        logger.info("finance.entry.added", debt_id=did, direction=direction.value)
        async with self.registry.session() as session:
            return await finance_repo.get(session, did)  # type: ignore[return-value]

    async def list_open(
        self, direction: DebtDirection | None = None
    ) -> list[DebtRecord]:
        """Return non-settled debt records, optionally filtered by direction."""
        async with self.registry.session() as session:
            return await finance_repo.list_open(session, direction=direction)

    async def delete(self, entry_id: int) -> bool:
        """Delete a debt record and cancel its reminder job (instant undo)."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            record = await finance_repo.get(session, entry_id)
            if record is None:
                return False
            job_id = record.reminder_job_id
            ok = await finance_repo.delete(session, entry_id)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("finance.entry.deleted", debt_id=entry_id)
        return ok

    async def settle(self, entry_id: int) -> DebtRecord | None:
        """Settle a debt record and cancel any pending reminder job."""
        from app.scheduler.jobs import cancel_job

        async with self.registry.session() as session:
            record = await finance_repo.get(session, entry_id)
            if record is None:
                return None
            job_id = record.reminder_job_id
            settled = await finance_repo.settle(session, entry_id)
        if job_id:
            cancel_job(self.registry.scheduler, job_id)
        logger.info("finance.entry.settled", debt_id=entry_id)
        return settled

    async def fire_debt_reminder(self, debt_id: int, role: str = "") -> None:
        """Notify the owner who owes whom how much (Uzbek)."""
        async with self.registry.session() as session:
            record = await finance_repo.get(session, debt_id)
            if record is None or record.status == DebtStatus.settled:
                return
            counterparty = await person_repo.get_by_id(
                session, record.counterparty_id
            )
            name = getattr(counterparty, "display_name", "kishi")
            direction = record.direction
            amount = record.amount
            currency = record.currency
            due_at = record.due_at
            description = record.description

        amount_str = self._format_amount(amount)
        when_str = self._local_str(due_at)
        if direction == DebtDirection.they_owe_me:
            line = f"{name} sizga {amount_str} {currency} qarzdor."
        else:
            line = f"Siz {name}ga {amount_str} {currency} qarzdorsiz."
        text = f"Qarz eslatmasi (muddat: {when_str}):\n{line}"
        if description:
            text += f"\nIzoh: {description}"

        notifier = self.registry.notification_service
        if notifier is not None:
            await notifier.notify_owner(text)

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _format_amount(amount: Decimal) -> str:
        """Format an amount without trailing zeros (e.g. 1000.00 -> 1000)."""
        normalized = amount.normalize()
        sign, digits, exponent = normalized.as_tuple()
        if exponent >= 0:
            return f"{int(normalized):,}".replace(",", " ")
        return f"{normalized:,.2f}".replace(",", " ")

    def _local_str(self, dt: datetime | None) -> str:
        if dt is None:
            return "-"
        return to_local_str(dt, self.registry.settings.user_timezone)
