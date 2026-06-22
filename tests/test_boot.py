"""Boot-wiring smoke tests: the app constructs and the owner is created."""

from __future__ import annotations

from app.bot.application import build_application
from app.repositories import person_repo


def test_settings_validate(settings):
    settings.require_runtime()  # must not raise with the test env in place
    assert settings.is_sqlite
    assert settings.sync_database_url.startswith("sqlite:///")


async def test_owner_is_created_and_idempotent(registry):
    # The fixture already ensured the owner; a second call must not duplicate it.
    async with registry.session() as session:
        await person_repo.ensure_owner(
            session, telegram_user_id=registry.settings.owner_chat_id, display_name="Owner"
        )
        owner = await person_repo.get_owner(session)
        everyone = await person_repo.list_all(session)

    assert owner is not None
    assert owner.is_owner is True
    assert owner.id == 1  # autoincrement PK works on SQLite
    assert len(everyone) == 1


def test_build_application_registers_handlers(registry):
    application = build_application(registry)
    total = sum(len(group) for group in application.handlers.values())
    assert total >= 4  # /start, /help, text, voice
    assert application.bot_data["registry"] is registry
