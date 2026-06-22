"""Contact-search tests — find by name, @username/nickname, alias, or phone."""

from __future__ import annotations

from app.brain.contacts import ContactMatch, resolve_contact
from app.repositories import person_repo


async def _seed(registry):
    async with registry.session() as session:
        await person_repo.create(
            session,
            display_name="Akmal Karimov",
            telegram_username="akmalk",
            phone="+998 90 123 45 67",
            aliases=["Akmaljon"],
        )
        await person_repo.create(
            session, display_name="Boburbek", phone="+998935557788"
        )


async def test_search_by_full_phone(registry):
    await _seed(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "998901234567")
    assert [p.display_name for p in hits] == ["Akmal Karimov"]


async def test_search_by_partial_and_formatted_phone(registry):
    await _seed(registry)
    async with registry.session() as session:
        tail = await person_repo.search_by_name(session, "1234567")
        formatted = await person_repo.search_by_name(session, "+998 90 123 45 67")
    assert any(p.display_name == "Akmal Karimov" for p in tail)
    assert any(p.display_name == "Akmal Karimov" for p in formatted)


async def test_search_by_username_and_alias_still_work(registry):
    await _seed(registry)
    async with registry.session() as session:
        by_user = await person_repo.search_by_name(session, "akmalk")
        by_alias = await person_repo.search_by_name(session, "Akmaljon")
        by_name = await person_repo.search_by_name(session, "Bobur")
    assert any(p.display_name == "Akmal Karimov" for p in by_user)
    assert any(p.display_name == "Akmal Karimov" for p in by_alias)
    assert any(p.display_name == "Boburbek" for p in by_name)


async def test_resolve_contact_by_phone(registry):
    async with registry.session() as session:
        await person_repo.create(
            session, display_name="Davron", phone="998901112233", telegram_user_id=555111
        )
    async with registry.session() as session:
        result = await resolve_contact(session, "998901112233")
    assert isinstance(result, ContactMatch)
    assert result.display_name == "Davron"


async def test_short_digit_query_is_not_a_phone_search(registry):
    """A 2-3 digit query must not match phones (would be too noisy)."""
    await _seed(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "99")
    # No name contains "99" and the query is too short for a phone match.
    assert hits == []


async def _seed_namesakes(registry):
    """A full-name contact plus a short-name contact that the first contains."""
    async with registry.session() as session:
        await person_repo.create(session, display_name="Asadbek Karimov")
        await person_repo.create(session, display_name="Asad")


async def test_exact_token_match_beats_substring(registry):
    """'Asadbek' resolves to 'Asadbek Karimov' (exact word), not 'Asad'."""
    await _seed_namesakes(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Asadbek")
    assert [p.display_name for p in hits] == ["Asadbek Karimov"]


async def test_short_exact_name_does_not_drag_in_longer_namesake(registry):
    """'Asad' resolves to exactly 'Asad', not the substring-y 'Asadbek Karimov'."""
    await _seed_namesakes(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Asad")
    assert [p.display_name for p in hits] == ["Asad"]


async def test_resolve_one_word_against_full_name_is_confident(registry):
    """A one-word query that exactly equals a contact's word is a confident pick."""
    await _seed_namesakes(registry)
    async with registry.session() as session:
        result = await resolve_contact(session, "Asadbek")
    assert isinstance(result, ContactMatch)
    assert result.display_name == "Asadbek Karimov"
    assert result.confidence == 1.0
