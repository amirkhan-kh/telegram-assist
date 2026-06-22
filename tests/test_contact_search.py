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


async def test_search_matches_saved_name(registry):
    """Search resolves on the owner-saved display name (substring / word token)."""
    await _seed(registry)
    async with registry.session() as session:
        by_name = await person_repo.search_by_name(session, "Bobur")
        by_token = await person_repo.search_by_name(session, "Karimov")
    assert any(p.display_name == "Boburbek" for p in by_name)
    assert any(p.display_name == "Akmal Karimov" for p in by_token)


async def test_username_only_match_is_not_returned(registry):
    """A contact reachable ONLY via its @username/alias must NOT surface.

    Real bug: a business "Florens" whose Telegram username embedded "doniyor"
    appeared under a "Doniyor" search. Contact search is by saved name + phone
    only — the handle is stored (for display) but never matched.
    """
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session,
            telegram_user_id=970,
            display_name="Florens Toshkent Italiyanski",
            username="doniyor_florens",
        )
    async with registry.session() as session:
        by_name = await person_repo.search_by_name(session, "Doniyor")
        by_handle = await person_repo.search_by_name(session, "doniyor_florens")
    assert by_name == []  # 'Doniyor' lives only in the username, not the name
    assert by_handle == []  # the @handle itself is not a search key


async def test_search_by_phone_still_works(registry):
    """Phone-number lookup is unaffected: a saved number resolves the contact."""
    await _seed(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "+998901234567")
    assert any(p.display_name == "Akmal Karimov" for p in hits)


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


# ── precision: look-alike namesakes must NOT be fuzzy-matched ──────────────────
async def _seed_doniyor_lookalikes(registry):
    """Names that merely *resemble* 'Doniyor' (Cyrillic) — must not be matched."""
    async with registry.session() as session:
        await person_repo.create(session, display_name="Дилёрбек Уч")
        await person_repo.create(session, display_name="Донербек Телфон")
        await person_repo.create(session, display_name="Kichkina Wahmardon Wopir")


async def test_lookalikes_are_not_returned_for_a_distinct_name(registry):
    """A 'Doniyor' search must not drag in different-rooted look-alikes.

    "Дилёрбек" (dilyor-, diverges at char 2) and "Донербек" (doner-, edit-sim 0.38
    to 'doniyor') and an unrelated business name all stay out — the screenshot bug.
    """
    await _seed_doniyor_lookalikes(registry)
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Doniyor")
    assert hits == []


async def test_honorific_suffix_does_not_break_resolution(registry):
    """'Doniyorbek og'am' resolves to the saved 'Doniyorbek', not look-alikes."""
    await _seed_doniyor_lookalikes(registry)
    async with registry.session() as session:
        await person_repo.upsert_telegram_contact(
            session, telegram_user_id=909, display_name="Doniyorbek"
        )
    async with registry.session() as session:
        result = await resolve_contact(session, "Doniyorbek og'am")
    assert isinstance(result, ContactMatch)
    assert result.display_name == "Doniyorbek"


async def test_name_stem_finds_all_namesakes(registry):
    """Searching the stem 'Doniyor' surfaces every namesake (Latin + Cyrillic).

    None of these is the bare token "Doniyor" itself, so the exact-match
    short-circuit does not fire and the substring step returns them all — the
    owner then picks among them by number.
    """
    async with registry.session() as session:
        await person_repo.create(session, display_name="Doniyorbek")
        await person_repo.create(session, display_name="Дониёрбек Ака")
        await person_repo.create(session, display_name="Doniyorjon")
        await person_repo.create(session, display_name="Akmal")  # unrelated
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Doniyor")
    names = {p.display_name for p in hits}
    assert names == {"Doniyorbek", "Дониёрбек Ака", "Doniyorjon"}


async def test_fuzzy_still_catches_a_real_typo(registry):
    """A genuine typo ('Azizilo' -> 'Azizillo') must still resolve."""
    async with registry.session() as session:
        await person_repo.create(session, display_name="Azizillo")
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Azizilo")
    assert [p.display_name for p in hits] == ["Azizillo"]


async def test_spelling_variant_recall_without_lookalikes(registry):
    """'Doniyor' finds the е/ё spelling variant 'Дониер' but NOT distinct names.

    "Дониер" (Cyrillic е) == "Doniyor"; the prop-prefix fuzzy bridges the е/ё
    gap, while same-prefix-but-different names ("Дилёрбек", "Донербек") stay out.
    """
    async with registry.session() as session:
        await person_repo.create(session, display_name="Дониер Хоз")
        await person_repo.create(session, display_name="Дилёрбек Уч")
        await person_repo.create(session, display_name="Донербек Телфон")
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Doniyor")
    names = {p.display_name for p in hits}
    assert "Дониер Хоз" in names
    assert "Дилёрбек Уч" not in names
    assert "Донербек Телфон" not in names


# ── clean_contact_query: strip honorifics / particles / plural before search ───
def test_clean_contact_query_strips_honorific_particle_plural():
    from app.brain.contacts import clean_contact_query

    assert clean_contact_query("Doniyorbek og'am") == "Doniyorbek"
    assert clean_contact_query("Doniyor lar") == "Doniyor"  # separate plural token
    assert clean_contact_query("Doniyorlar") == "Doniyor"   # glued plural suffix
    assert clean_contact_query("Doniyoraka") == "Doniyor"   # glued honorific
    assert clean_contact_query("Doniyoruka") == "Doniyor"   # glued honorific
    assert clean_contact_query("Akmal aka") == "Akmal"
    assert clean_contact_query("Azizbek") == "Azizbek"      # nothing to strip
    assert clean_contact_query("Malika") == "Malika"        # real name not mangled


def test_separated_and_glued_honorifics_reduce_identically():
    """'Doniyor aka' and 'Doniyoraka' (and og'am variants) must yield one stem.

    Covers aka/uka/opa/singil/og'am, written WITH a space or GLUED — both the
    lookup (clean_contact_query) and send (_strip_honorifics) paths.
    """
    from app.brain.contacts import _strip_honorifics, clean_contact_query

    pairs = [
        ("Doniyor aka", "Doniyoraka"),
        ("Doniyor uka", "Doniyoruka"),
        ("Doniyor opa", "Doniyoropa"),
        ("Doniyor singil", "Doniyorsingil"),
        ("Doniyor og'am", "Doniyorog'am"),
    ]
    for separated, glued in pairs:
        assert clean_contact_query(separated) == clean_contact_query(glued) == "Doniyor"
        assert _strip_honorifics(separated) == _strip_honorifics(glued) == "Doniyor"
    # Name-forming syllables are never mistaken for honorifics.
    for keep in ("Akmaljon", "Doniyorbek", "Eshonxoja", "Maya"):
        assert clean_contact_query(keep) == keep


async def test_glued_honorific_in_contact_name_is_matched(registry):
    """A contact saved with a GLUED honorific is found by its core name.

    The real case: "Донерака ОГАМ 2021" (Doner+aka) couldn't be reached via
    "Doniyor" until the contact side also indexed the de-glued stem "Донер".
    """
    async with registry.session() as session:
        await person_repo.create(session, display_name="Донерака ОГАМ 2021")
        await person_repo.create(session, display_name="Анварака Doktor")  # off-name
    async with registry.session() as session:
        hits = await person_repo.search_by_name(session, "Doniyor")
    names = {p.display_name for p in hits}
    assert "Донерака ОГАМ 2021" in names      # doner ~ doniyor (sim 0.57)
    assert "Анварака Doktor" not in names      # anvar is unrelated to doniyor


async def test_typo_queries_still_resolve(registry):
    """Misspellings (dropped/swapped/extra letters) still find the right contact.

    'Donyor'/'Donior' (a letter off), 'Doniyoraka' (glued honorific) all reach a
    saved 'Doniyorbek' — surfaced as a candidate to pick from.
    """
    from app.brain.contacts import clean_contact_query

    async with registry.session() as session:
        await person_repo.create(session, display_name="Doniyorbek")
        await person_repo.create(session, display_name="Akmal Karimov")  # unrelated
    for typo in ("Donyor", "Donior", "Doniyoraka"):
        async with registry.session() as session:
            hits = await person_repo.search_by_name(
                session, clean_contact_query(typo)
            )
        names = {p.display_name for p in hits}
        assert "Doniyorbek" in names, typo
        assert "Akmal Karimov" not in names, typo
