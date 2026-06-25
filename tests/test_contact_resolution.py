"""Contact resolution: phone search, namesake disambiguation (phone-distinguished
+ numbered buttons), button pick, and raw-phone import. All offline."""

from __future__ import annotations

from datetime import UTC, datetime

from app.bot.keyboards import contact_pick_keyboard
from app.brain.intent_router import RoutedIntent
from app.brain.intents import SendMessage
from app.db.models.enums import SendMode
from app.repositories import person_repo
from app.services import dispatcher
from app.services.dispatcher import _looks_like_phone, dispatch


def _now() -> datetime:
    return datetime(2026, 6, 18, 8, 0, tzinfo=UTC)


def _send(recipient: str, content: str = "salom") -> RoutedIntent:
    return RoutedIntent(
        "send_message", SendMessage(recipient_name=recipient, content=content), {}
    )


async def _two_phoned_akmals(registry) -> tuple[int, int, int]:
    """Two same-name contacts with DIFFERENT phones; return (owner_key, id1, id2)."""
    async with registry.session() as session:
        p1 = await person_repo.upsert_telegram_contact(
            session, telegram_user_id=601, display_name="Akmal", phone="+998901112233"
        )
        p2 = await person_repo.upsert_telegram_contact(
            session, telegram_user_id=602, display_name="Akmal", phone="+998904445566"
        )
        ids = (p1.id, p2.id)
    return registry.settings.owner_chat_id, ids[0], ids[1]


# ── _looks_like_phone ─────────────────────────────────────────────────────────
def test_looks_like_phone():
    assert _looks_like_phone("+998911234567")
    assert _looks_like_phone("901234567")
    assert _looks_like_phone("+998 91 123 45 67")
    assert not _looks_like_phone("Akmal")
    assert not _looks_like_phone("Akmal 2021")  # a name with a trailing year
    assert not _looks_like_phone("")


# ── contact_pick_keyboard ─────────────────────────────────────────────────────
def test_contact_pick_keyboard_encodes_person_ids():
    kb = contact_pick_keyboard([11, 22, 33])
    flat = [b for row in kb.inline_keyboard for b in row]
    assert [b.text for b in flat] == ["1", "2", "3"]
    assert [b.callback_data for b in flat] == ["pick:11", "pick:22", "pick:33"]


# ── disambiguation distinguishes identical names by phone + offers buttons ─────
async def test_disambiguation_shows_phone_and_buttons(registry):
    owner_key, _, _ = await _two_phoned_akmals(registry)
    res = await dispatch(registry, _send("Akmal"), now=_now())
    # Both phones are shown so the two identical names are tellable apart.
    assert "+998901112233" in res.text and "+998904445566" in res.text
    # Inline numbered pick buttons are attached.
    assert res.reply_markup is not None
    payloads = [b.callback_data for row in res.reply_markup.inline_keyboard for b in row]
    assert all(p.startswith("pick:") for p in payloads) and len(payloads) == 2
    assert dispatcher.has_pending(owner_key)


# ── button pick (resume_choice_pid) resumes the paused send ───────────────────
async def test_resume_choice_pid_selects_contact(registry):
    owner_key, id1, _ = await _two_phoned_akmals(registry)
    await dispatch(registry, _send("Akmal"), now=_now())

    res = await dispatcher.resume_choice_pid(registry, id1, now=_now())
    assert res is not None
    assert "Qanday yuboray" in res.text  # proceeds to the voice/text channel ask
    assert not dispatcher.has_pending(owner_key)

    out = await dispatcher.complete_outbound(registry, owner_key, SendMode.text)
    assert "xabar yuborildi" in out.text


async def test_resume_choice_pid_stale_or_missing(registry):
    owner_key, id1, _ = await _two_phoned_akmals(registry)
    await dispatch(registry, _send("Akmal"), now=_now())
    # An id not among the offered candidates -> stale note, no crash.
    res = await dispatcher.resume_choice_pid(registry, 999999, now=_now())
    assert res is not None and "eskirgan" in res.text
    # Nothing pending -> None (caller treats the tap as a no-op).
    dispatcher.clear_pending(owner_key)
    assert await dispatcher.resume_choice_pid(registry, id1, now=_now()) is None


# ── phone addressing: a saved contact resolves straight by its number ─────────
async def test_phone_addresses_saved_contact_directly(registry):
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=701, display_name="Dilshod", phone="+998901234567"
        )
    owner_key = registry.settings.owner_chat_id
    res = await dispatch(registry, _send("+998901234567"), now=_now())
    # Resolved to one contact -> straight to the channel ask, no disambiguation.
    assert "Qanday yuboray" in res.text
    assert "Dilshod" in res.text
    assert not dispatcher.has_pending(owner_key)


# ── raw phone not in contacts -> import as a contact, then send ────────────────
async def test_unsaved_phone_is_imported_then_sent(registry, monkeypatch):
    registry.userbot = object()  # truthy so the import path is taken

    async def _no_sync(*_a, **_k):
        return 0

    async def _fake_import(_client, phone):
        return {"user_id": 9999, "name": "Yangi Raqam", "username": None, "phone": phone}

    monkeypatch.setattr("app.userbot.contacts.sync_contacts", _no_sync)
    monkeypatch.setattr("app.userbot.contacts.import_phone_contact", _fake_import)

    res = await dispatch(registry, _send("+998999998877"), now=_now())
    assert "Qanday yuboray" in res.text and "Yangi Raqam" in res.text
    # The imported number is now a saved contact.
    async with registry.session() as session:
        person = await person_repo.get_by_telegram_user_id(session, 9999)
    assert person is not None and person.display_name == "Yangi Raqam"


async def test_unsaved_phone_prompts_save_after_delivery(registry, monkeypatch):
    registry.userbot = object()

    async def _no_sync(*_a, **_k):
        return 0

    async def _fake_import(_client, phone):
        return {"user_id": 9998, "name": "Yangi Raqam", "username": None, "phone": phone}

    monkeypatch.setattr("app.userbot.contacts.sync_contacts", _no_sync)
    monkeypatch.setattr("app.userbot.contacts.import_phone_contact", _fake_import)

    owner_key = registry.settings.owner_chat_id
    res = await dispatch(registry, _send("+998999998800"), now=_now())
    assert "Qanday yuboray" in res.text

    out = await dispatcher.complete_outbound(registry, owner_key, SendMode.text)

    assert "xabar yuborildi" in out.text
    assert "saqlab qo'yaymi" in out.text
    payloads = [b.callback_data for row in out.reply_markup.inline_keyboard for b in row]
    assert payloads == ["savephone:yes", "savephone:no"]


async def test_full_raw_phone_is_not_rewritten_to_another_saved_contact(
    registry, monkeypatch
):
    registry.userbot = object()
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=909902440,
            display_name="Wrong Saved Contact",
            phone="+998909902440",
        )

    async def _no_sync(*_a, **_k):
        return 0

    async def _fake_import(_client, phone):
        assert phone == "+998908408407"
        return {
            "user_id": 8408407,
            "name": "Exact Raw Phone",
            "username": None,
            "phone": phone,
        }

    monkeypatch.setattr("app.userbot.contacts.sync_contacts", _no_sync)
    monkeypatch.setattr("app.userbot.contacts.import_phone_contact", _fake_import)

    res = await dispatch(registry, _send("+998908408407"), now=_now())

    assert "Qanday yuboray" in res.text
    assert "Exact Raw Phone" in res.text
    assert "Wrong Saved Contact" not in res.text


async def test_unsaved_phone_not_on_telegram_reports_not_found(registry, monkeypatch):
    registry.userbot = object()

    async def _no_sync(*_a, **_k):
        return 0

    async def _no_import(_client, _phone):
        return None  # number isn't on Telegram / privacy-hidden

    monkeypatch.setattr("app.userbot.contacts.sync_contacts", _no_sync)
    monkeypatch.setattr("app.userbot.contacts.import_phone_contact", _no_import)

    res = await dispatch(registry, _send("+998000000000"), now=_now())
    assert "topilmadi" in res.text


# ── userbot import_phone_contact helper (fake Telethon client) ────────────────
class _FakeUser:
    def __init__(self, uid, first="", last="", username=None, phone=None):
        self.id = uid
        self.first_name = first
        self.last_name = last
        self.username = username
        self.phone = phone


class _FakeImportResult:
    def __init__(self, users):
        self.users = users


class _FakeTgClient:
    """An async-callable Telethon stand-in: ``await client(request)`` -> result."""

    def __init__(self, users):
        self._users = users

    async def __call__(self, _request):
        return _FakeImportResult(self._users)


async def test_import_phone_contact_returns_user_dict():
    from app.userbot.contacts import import_phone_contact

    client = _FakeTgClient([_FakeUser(7, "Yangi", "Mijoz", username="newguy")])
    info = await import_phone_contact(client, "+998 90 111 22 33")
    assert info == {
        "user_id": 7,
        "name": "Yangi Mijoz",
        "username": "newguy",
        "phone": "998901112233",
    }


async def test_import_phone_contact_none_when_not_on_telegram():
    from app.userbot.contacts import import_phone_contact

    assert await import_phone_contact(_FakeTgClient([]), "+998999999999") is None
    # Too few digits to be a real number -> skip the import entirely.
    assert await import_phone_contact(_FakeTgClient([]), "12") is None


# ── "show contacts" -> numbered pick -> compose a message ─────────────────────
def _list(query: str) -> RoutedIntent:
    from app.brain.intents import ListContacts

    return RoutedIntent("list_contacts", ListContacts(query=query), {})


async def test_show_contacts_pick_then_compose_then_send(registry):
    """'Akmal lar kontaktlarini ko'rsat' -> numbered list -> pick -> write -> send."""
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=850, display_name="Akmaljon", phone="+998901112200"
        )
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=851, display_name="Akmalbek", phone="+998901112201"
        )
    owner_key = registry.settings.owner_chat_id

    # The plural "lar" is stripped; the search returns both Akmal* contacts.
    res = await dispatch(registry, _list("Akmal lar"), now=_now())
    assert "Kimga xabar yuboramiz" in res.text
    assert "Akmaljon" in res.text and "Akmalbek" in res.text
    assert res.reply_markup is not None
    assert dispatcher.has_pending(owner_key)

    # Picking #1 arms a compose (no resumable action — it awaits the body).
    res2 = await dispatcher.resume_choice(registry, 1, now=_now())
    assert res2 is not None and "xabaringizni" in res2.text
    assert dispatcher.has_pending_compose(owner_key)
    assert not dispatcher.has_pending(owner_key)

    # The next message is the body -> the normal send flow asks the channel.
    res3 = await dispatcher.dispatch_compose(
        registry, "ertaga kelasanmi?", now=_now()
    )
    assert res3 is not None and "Qanday yuboray" in res3.text
    assert not dispatcher.has_pending_compose(owner_key)

    out = await dispatcher.complete_outbound(registry, owner_key, SendMode.text)
    assert "yuborildi" in out.text


async def test_show_contacts_button_pick_arms_compose(registry):
    """Tapping a numbered pick button (pick:<id>) also begins the compose."""
    async with registry.session() as session:
        p = await person_repo.upsert_telegram_contact(
            session, telegram_user_id=860, display_name="Sardorbek", phone="+998901110000"
        )
        pid = p.id
    owner_key = registry.settings.owner_chat_id
    await dispatch(registry, _list("Sardor"), now=_now())
    res = await dispatcher.resume_choice_pid(registry, pid, now=_now())
    assert res is not None and "xabaringizni" in res.text
    assert dispatcher.has_pending_compose(owner_key)


async def test_show_contacts_not_found_is_clean(registry):
    res = await dispatch(registry, _list("Nobodyxyzzz"), now=_now())
    assert "topilmadi" in res.text
    assert not dispatcher.has_pending(registry.settings.owner_chat_id)


async def test_dispatch_compose_without_pending_returns_none(registry):
    dispatcher.clear_pending_compose(registry.settings.owner_chat_id)
    assert await dispatcher.dispatch_compose(registry, "salom", now=_now()) is None
