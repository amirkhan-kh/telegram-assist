"""Tests for phone/Telegram contact resolution and the voice/formal send flow.

Covers the path the owner uses to message a saved contact by name: contact sync
into the DB, name resolution (including the sync-on-miss fallback), and the
``send_message`` dispatch with its voice/test-mode confirmation notes. All
offline — a fake Telethon client supplies the address book.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.brain.contacts import ContactMatch, resolve_contact
from app.brain.intent_router import RoutedIntent
from app.brain.intents import Formality, SendMessage
from app.db.models.enums import SendMode
from app.repositories import person_repo
from app.services.dispatcher import _resolve_recipient, complete_outbound, dispatch
from app.userbot.contacts import sync_contacts


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


# ── fake Telethon client returning a canned contacts result ───────────────────
class _FakeUser:
    def __init__(
        self,
        uid: int,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
        phone: str | None = None,
        bot: bool = False,
        deleted: bool = False,
    ) -> None:
        self.id = uid
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.phone = phone
        self.bot = bot
        self.deleted = deleted


class _FakeContacts:
    def __init__(self, users: list) -> None:
        self.users = users


class _FakeUserbot:
    """Minimal Telethon stand-in: calling it runs the GetContactsRequest."""

    def __init__(self, users: list) -> None:
        self._users = users

    async def __call__(self, _request: object) -> _FakeContacts:
        return _FakeContacts(self._users)


# ── person_repo.upsert_telegram_contact ───────────────────────────────────────
async def test_upsert_creates_then_updates(registry):
    async with registry.session() as session:
        created = await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=555,
            display_name="Akmal Karimov",
            username="akmalk",
            phone="+998901112233",
        )
        assert created.display_name == "Akmal Karimov"
        assert created.telegram_user_id == 555
        assert "akmalk" in (created.aliases or [])

        # Re-syncing the same Telegram id updates the row, not duplicates it.
        again = await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=555,
            display_name="Akmal Karimov",
            username="akmal_new",
        )
        assert again.id == created.id
        people = await person_repo.list_all(session)
    assert sum(1 for p in people if p.telegram_user_id == 555) == 1


async def test_upsert_never_clobbers_owner_name(registry):
    owner_chat = registry.settings.owner_chat_id
    async with registry.session() as session:
        updated = await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=owner_chat,
            display_name="Some Synced Name",
        )
    assert updated.is_owner is True
    assert updated.display_name == "Owner"  # preserved, not overwritten


# ── sync_contacts ──────────────────────────────────────────────────────────────
async def test_sync_contacts_imports_named_humans_only(registry):
    userbot = _FakeUserbot(
        [
            _FakeUser(101, first_name="Akmal", last_name="Karimov", username="akmalk"),
            _FakeUser(102, first_name="Bekzod"),
            _FakeUser(103, username="some_bot", bot=True),  # bot -> skipped
            _FakeUser(104, deleted=True),  # deleted -> skipped
            _FakeUser(105),  # nameless -> skipped
        ]
    )
    count = await sync_contacts(userbot, registry)
    assert count == 2
    async with registry.session() as session:
        akmal = await person_repo.get_by_telegram_user_id(session, 101)
        bekzod = await person_repo.get_by_telegram_user_id(session, 102)
    assert akmal is not None and akmal.display_name == "Akmal Karimov"
    assert bekzod is not None and bekzod.display_name == "Bekzod"


# ── _resolve_recipient: sync-on-miss fallback ─────────────────────────────────
async def test_resolve_recipient_syncs_on_miss(registry):
    # "Bekzod" is not in the DB yet; the userbot address book knows them.
    registry.userbot = _FakeUserbot([_FakeUser(202, first_name="Bekzod")])
    resolved = await _resolve_recipient(registry, "Bekzod")
    assert isinstance(resolved, ContactMatch)
    assert resolved.display_name == "Bekzod"
    assert resolved.chat_id == 202


# ── honorific-aware resolution ────────────────────────────────────────────────
async def test_resolve_strips_honorific(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=303, display_name="Akmal Karimov"
        )
        # "Akmal aka" must still resolve to the saved "Akmal Karimov".
        resolved = await resolve_contact(session, "Akmal aka")
    assert isinstance(resolved, ContactMatch)
    assert resolved.chat_id == 303


# ── dispatch(send_message) end-to-end ─────────────────────────────────────────
async def test_send_message_asks_channel_then_confirms_with_notes(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=777, display_name="Dilshod"
        )
    routed = RoutedIntent(
        "send_message",
        SendMessage(recipient_name="Dilshod", content="Assalomu alaykum, ertaga uchrashamizmi?"),
        {},
    )
    # The send first asks the owner which channel to use.
    prompt = await dispatch(registry, routed, now=_now())
    assert "Dilshod" in prompt.text
    assert "Qanday yuboray" in prompt.text
    assert prompt.reply_markup is not None

    # Picking voice delivers it, with the usual fallback/test-mode notes.
    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.voice
    )
    assert "Dilshod" in result.text
    # No TTS provider in tests -> the owner is told it falls back to text.
    assert "matn shaklida" in result.text
    # TEST_MODE is on in tests -> the owner is told it was a preview redirect.
    assert "TEST rejimi" in result.text


async def test_send_message_schedules_no_job_no_duplicate(registry):
    # Immediate sends must NOT register a scheduler job — otherwise the job
    # (run_at=now) fires while send_now is still delivering and double-sends.
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=778, display_name="Jasur"
        )
    before = len(registry.scheduler.get_jobs())
    routed = RoutedIntent(
        "send_message", SendMessage(recipient_name="Jasur", content="salom"), {}
    )
    prompt = await dispatch(registry, routed, now=_now())
    assert "Qanday yuboray" in prompt.text
    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.text
    )
    after = len(registry.scheduler.get_jobs())
    assert "yuborildi" in result.text
    assert after == before  # immediate send creates no scheduled_message job


async def test_explicit_voice_skips_the_channel_prompt(registry):
    """When the owner says 'ovozli', the bot sends straight away (no buttons)."""
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=780, display_name="Kamol"
        )
    routed = RoutedIntent(
        "send_message",
        SendMessage(recipient_name="Kamol", content="salom", delivery="voice"),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "Qanday yuboray" not in result.text
    assert "yuborildi" in result.text


async def test_unspecified_delivery_asks_with_buttons(registry):
    """The default 'ask' delivery shows the voice/text choice prompt."""
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=781, display_name="Sardor"
        )
    routed = RoutedIntent(
        "send_message", SendMessage(recipient_name="Sardor", content="salom"), {}
    )
    # No delivery field -> defaults to 'ask'.
    assert routed.params.delivery.value == "ask"
    result = await dispatch(registry, routed, now=_now())
    assert "Qanday yuboray" in result.text


async def test_complete_outbound_without_pending_is_graceful(registry):
    from app.services.dispatcher import clear_pending_outbound

    clear_pending_outbound(registry.settings.owner_chat_id)
    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.text
    )
    assert "eskirgan" in result.text


async def test_schedule_message_asks_channel_then_schedules(registry):
    from app.brain.intents import ScheduleMessage, TimeSpec

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=779, display_name="Olim"
        )
    before = len(registry.scheduler.get_jobs())
    routed = RoutedIntent(
        "schedule_message",
        ScheduleMessage(
            recipient_name="Olim",
            content="ertangi yig'ilish esingizda bo'lsin",
            when=TimeSpec(raw="ertaga soat 10"),
        ),
        {},
    )
    prompt = await dispatch(registry, routed, now=_now())
    assert "Qanday yuboray" in prompt.text

    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.voice
    )
    after = len(registry.scheduler.get_jobs())
    assert "rejalashtirildi" in result.text
    assert after == before + 1  # the scheduled_message delivery job is registered


async def test_send_message_unknown_contact_is_graceful(registry):
    routed = RoutedIntent(
        "send_message",
        SendMessage(recipient_name="Notincontacts", content="salom"),
        {},
    )
    result = await dispatch(registry, routed, now=_now())
    assert "topilmadi" in result.text


# ── meeting notice: deliver now AND again at the meeting time ─────────────────
async def test_meeting_notice_sends_now_and_schedules(registry):
    """'uchrashuv haqida xabar ber' -> message goes out now + at the meeting time."""
    from app.brain.intents import ScheduleMessage, TimeSpec

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=811, display_name="Doniyor"
        )
    before = len(registry.scheduler.get_jobs())
    routed = RoutedIntent(
        "schedule_message",
        ScheduleMessage(
            recipient_name="Doniyor",
            content="Ertaga soat 9:35 dagi uchrashuvimizni eslatib qo'yaman.",
            when=TimeSpec(raw="ertaga soat 9:35"),
            meeting_notice=True,
        ),
        {},
    )
    prompt = await dispatch(registry, routed, now=_now())
    assert "Qanday yuboray" in prompt.text

    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.text
    )
    after = len(registry.scheduler.get_jobs())
    # Confirms both deliveries; the future copy registers exactly one job (the
    # immediate copy sends inline with no job, so it can't double-fire).
    assert "hozir yuborildi" in result.text
    assert "yana yuboriladi" in result.text
    assert after == before + 1


async def test_plain_schedule_message_is_not_a_meeting_notice(registry):
    """Without meeting_notice the message is only scheduled (no immediate send)."""
    from app.brain.intents import ScheduleMessage, TimeSpec

    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=812, display_name="Olima"
        )
    routed = RoutedIntent(
        "schedule_message",
        ScheduleMessage(
            recipient_name="Olima",
            content="ertaga uchrashamizmi?",
            when=TimeSpec(raw="ertaga soat 10"),
        ),
        {},
    )
    await dispatch(registry, routed, now=_now())
    result = await complete_outbound(
        registry, registry.settings.owner_chat_id, SendMode.text
    )
    assert "rejalashtirildi" in result.text
    assert "hozir yuborildi" not in result.text


def test_schedule_message_tool_exposes_meeting_fields():
    from app.brain.tools import build_tools

    sched = {t["name"]: t for t in build_tools()}["schedule_message"]
    props = sched["input_schema"]["properties"]
    assert "meeting_notice" in props and props["meeting_notice"]["type"] == "boolean"
    assert "create_meet_link" in props
    # Anthropic requires every property be listed as required.
    for field in ("meeting_notice", "create_meet_link"):
        assert field in sched["input_schema"]["required"]


def test_schedule_message_meeting_fields_default_false():
    from app.brain.intents import ScheduleMessage, TimeSpec

    msg = ScheduleMessage(
        recipient_name="x", content="y", when=TimeSpec(raw="ertaga soat 9")
    )
    assert msg.meeting_notice is False
    assert msg.create_meet_link is False


# ── formality field ───────────────────────────────────────────────────────────
def test_formality_defaults_neutral_and_accepts_formal():
    assert SendMessage(recipient_name="x", content="y").formality is Formality.neutral
    formal = SendMessage(recipient_name="x", content="y", formality="formal")
    assert formal.formality is Formality.formal


def test_send_tools_expose_formality():
    from app.brain.tools import build_tools

    by_name = {t["name"]: t for t in build_tools()}
    for name in ("send_message", "schedule_message"):
        props = by_name[name]["input_schema"]["properties"]
        assert "formality" in props
        assert props["formality"]["enum"] == ["neutral", "formal"]
