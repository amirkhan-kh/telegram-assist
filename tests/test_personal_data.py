"""Personal-data flow tests — date parsing + computed renewal/expiry events."""

from __future__ import annotations

from datetime import date

from app.brain.time_parse import parse_date
from app.db.models.enums import EventCategory
from app.repositories import person_repo


def test_parse_date_formats():
    assert parse_date("15.03.2019") == date(2019, 3, 15)
    assert parse_date("15/03/2019") == date(2019, 3, 15)
    assert parse_date("15-03-2019") == date(2019, 3, 15)
    assert parse_date("2019-03-15") == date(2019, 3, 15)
    assert parse_date(" 1.1.2020 ") == date(2020, 1, 1)


def test_parse_date_invalid():
    assert parse_date("salom") is None
    assert parse_date("32.01.2020") is None   # no 32nd day
    assert parse_date("15.13.2020") is None   # no 13th month
    assert parse_date("") is None


async def test_passport_renewal_is_ten_years_one_off(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_personal_date(
        owner_id=owner.id, kind="passport", base_date=date(2019, 3, 15)
    )
    assert event.event_date == date(2029, 3, 15)   # issue + 10 years
    assert event.yearly is False                   # one-off (re-enter after renew)
    assert event.category == EventCategory.document
    assert event.remind_days_before == [1, 7, 30]  # cleaned + sorted


async def test_inspection_uses_expiry_date_directly(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_personal_date(
        owner_id=owner.id, kind="inspection", base_date=date(2030, 6, 10)
    )
    assert event.event_date == date(2030, 6, 10)   # entered expiry date, no offset
    assert event.yearly is True                    # annual obligation


async def test_insurance_uses_expiry_date_directly(registry):
    async with registry.session() as session:
        owner = await person_repo.get_owner(session)
    event = await registry.event_service.add_personal_date(
        owner_id=owner.id, kind="insurance", base_date=date(2030, 1, 1)
    )
    assert event.event_date == date(2030, 1, 1)   # entered expiry date, no offset
    assert event.yearly is True
    assert event.category == EventCategory.document
