"""Repository for :class:`app.db.models.person.Person`.

The single owner is the contact with ``is_owner=True``. Contact search is
case-insensitive over the saved ``display_name`` and — for digit queries — the
``phone`` number. The Telegram @username/aliases are stored (for display) but are
NOT searched: matching only the owner-saved name avoids surfacing a contact whose
handle merely embeds the query.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.person import Person

# Typo/spelling-variant fallback, by edit-distance similarity. A name search must
# tolerate dropped/added/swapped/wrong letters (1-3 of them) yet still keep clearly
# different names out, so it accepts a contact when an edit-distance similarity
# (1 - levenshtein/maxlen) over the saved name or any word token clears
# ``_FUZZY_SIM`` AND the two share at least ``_FUZZY_MIN_PREFIX`` leading chars.
# The prefix floor is the precision lever. Three is the sweet spot: it admits
# same-root variants ("doniyor" ~ "doner"/"donyor"/"donier", prefix 3-4) while
# rejecting coincidental look-alikes that share only two leading chars but score
# well otherwise ("doniyor" vs "doktor"=do-, "dilyor"=d-). Ranked best-first.
_FUZZY_SIM = 0.55
_FUZZY_MIN_PREFIX = 3
_FUZZY_MIN_QUERY = 3
# How many ranked fuzzy candidates search may return. Kept generous so a common
# first name with many spelling variants (the owner has ~30 "Doner/Doniyor"-family
# contacts) surfaces them ALL; the caller caps the visible list (a send pick shows
# 8, a "show contacts" lookup up to 30).
_FUZZY_MAX = 50


def _shared_prefix_len(a: str, b: str) -> int:
    """Number of leading characters ``a`` and ``b`` have in common."""
    n = 0
    for ca, cb in zip(a, b, strict=False):
        if ca != cb:
            break
        n += 1
    return n


def _levenshtein(a: str, b: str) -> int:
    """Edit distance between two short strings (insert/delete/substitute = 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return prev[-1]


def _similarity(a: str, b: str) -> float:
    """1 - normalized edit distance: 1.0 identical, 0.0 completely different."""
    if not a and not b:
        return 1.0
    return 1.0 - _levenshtein(a, b) / max(len(a), len(b))
# A query with at least this many digits is treated as a phone-number search.
_PHONE_MIN_DIGITS = 4


def _digits(value: str | None) -> str:
    """Strip everything but digits from a phone string ('+998 90…' -> '99890…')."""
    return re.sub(r"\D", "", value or "")


async def get_owner(session: AsyncSession) -> Person | None:
    """Return the owner contact (``is_owner=True``) if one exists."""
    result = await session.execute(select(Person).where(Person.is_owner.is_(True)))
    return result.scalars().first()


async def get_by_id(session: AsyncSession, pid: int) -> Person | None:
    """Return a person by primary key, or ``None``."""
    return await session.get(Person, pid)


async def get_by_telegram_user_id(
    session: AsyncSession, uid: int
) -> Person | None:
    """Return a person by their Telegram user id, or ``None``."""
    result = await session.execute(
        select(Person).where(Person.telegram_user_id == uid)
    )
    return result.scalars().first()


async def all_people(session: AsyncSession) -> list[Person]:
    """Return all known people."""
    return list((await session.execute(select(Person))).scalars().all())


def _name_variants(person: Person) -> set[str]:
    """Normalized comparison keys for a person: the SAVED NAME, whole + per word.

    Only the owner-saved ``display_name`` is matched on (the phone is handled
    separately). The Telegram @username and aliases are deliberately NOT searched:
    a contact whose handle merely embeds the query — e.g. a business "Florens"
    whose username contains "doniyor" — must never surface under that name. The
    owner addresses people by the name they saved, or by phone number.

    Tokenizing lets a one-word query ("akmal") match a multi-word saved name
    ("Akmal Karimov"), and gives the fuzzy fallback per-word targets.
    """
    from app.brain.contacts import _strip_glued_honorific
    from app.brain.translit import normalize_name

    raw = person.display_name or ""
    out: set[str] = set()
    whole = normalize_name(raw)
    if whole:
        out.add(whole)
    for token in raw.split():
        tok = normalize_name(token)
        if not tok:
            continue
        out.add(tok)
        # Also index the honorific-stripped stem of a glued token, so a contact
        # saved as "Донерака"/"Анварака" is found by its core name ("Донер"/"Анвар")
        # — mirroring how the QUERY side strips honorifics.
        deglued = _strip_glued_honorific(tok)
        if deglued != tok and len(deglued) >= 3:
            out.add(deglued)
    return out


async def search_by_name(session: AsyncSession, name: str) -> list[Person]:
    """Search contacts by saved NAME or phone number (not @username/alias).

    1. If the query is mostly digits (a phone search), match it against each
       contact's digit-normalized ``phone`` (substring, so partial/last digits
       work): "+998 90 123 45 67", "901234567" or just the tail all resolve.
    2. Normalized (Cyrillic↔Latin, alphanumerics-only) substring match over the
       saved name and its word tokens, so "Акмал" finds "Akmal". The Telegram
       @username/aliases are NOT matched (so a handle that embeds the query can't
       surface an unrelated contact).
    3. If nothing matched, a fuzzy fallback by string similarity surfaces close
       spellings ("Azizilo" → "Azizillo", missing/extra letters), ranked best
       first — so the dispatcher can offer them as numbered choices.
    """
    from app.brain.translit import normalize_name

    term = name.strip()
    if not term:
        return []
    needle = normalize_name(term)
    lowered = term.lower()
    query_digits = _digits(term)

    # The address book is modest; scan in Python so transliteration + similarity
    # apply uniformly (SQL can't portably normalize/fuzzy-match across scripts).
    all_people = (await session.execute(select(Person))).scalars().all()

    # Phone-number search: when the query carries enough digits, match on phone.
    if len(query_digits) >= _PHONE_MIN_DIGITS:
        phone_hits = [
            p
            for p in all_people
            if (pd := _digits(p.phone)) and query_digits in pd
        ]
        if phone_hits:
            return phone_hits

    # Exact match on a whole name or a single word token wins decisively: a
    # one-word query ("Asadbek") resolves to the contact whose name/token is
    # exactly that, never every contact that merely *contains* the substring
    # ("Asad" → "Asadbek", "Murasad"…). Only fall through to substring/fuzzy
    # when there is no exact hit at all.
    if needle:
        exact_hits = [p for p in all_people if needle in _name_variants(p)]
        if exact_hits:
            return exact_hits

    matches: dict[int, Person] = {}
    for person in all_people:
        variants = _name_variants(person)
        if needle and any(needle in variant for variant in variants):
            matches[person.id] = person
        elif lowered and lowered in (person.display_name or "").lower():
            matches[person.id] = person
    if matches:
        return list(matches.values())

    # Fuzzy fallback: closest spellings (typos / missing letters) only. Each
    # candidate variant must clear the ratio threshold AND share a real prefix
    # AND be of comparable length, so genuine typos ("Azizilo"->"Azizillo")
    # surface while distinct look-alikes ("Doniyor" vs "Dilyorbek") do not.
    if not needle or len(needle) < _FUZZY_MIN_QUERY:
        return []
    scored: list[tuple[float, Person]] = []
    for person in all_people:
        best = 0.0
        for variant in _name_variants(person):
            if _shared_prefix_len(needle, variant) < _FUZZY_MIN_PREFIX:
                continue
            sim = _similarity(needle, variant)
            if sim >= _FUZZY_SIM:
                best = max(best, sim)
        if best:
            scored.append((best, person))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [person for _score, person in scored[:_FUZZY_MAX]]


async def ensure_owner(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    display_name: str,
) -> Person:
    """Return the owner, creating/promoting one if absent.

    If a person already exists for ``telegram_user_id`` they are marked as the
    owner; otherwise a new owner person is created.
    """
    owner = await get_owner(session)
    if owner is not None:
        return owner

    existing = await get_by_telegram_user_id(session, telegram_user_id)
    if existing is not None:
        existing.is_owner = True
        await session.flush()
        await session.refresh(existing)
        return existing

    return await create(
        session,
        display_name=display_name,
        telegram_user_id=telegram_user_id,
        is_owner=True,
    )


async def create(session: AsyncSession, **fields: Any) -> Person:
    """Create and flush a new person, returning the refreshed row."""
    person = Person(**fields)
    session.add(person)
    await session.flush()
    await session.refresh(person)
    return person


async def rename(session: AsyncSession, person_id: int, display_name: str) -> Person | None:
    """Rename a person/contact."""
    person = await get_by_id(session, person_id)
    if person is None:
        return None
    name = (display_name or "").strip()
    if not name:
        return person
    person.display_name = name
    await session.flush()
    await session.refresh(person)
    return person


async def forget_phone(session: AsyncSession, person_id: int) -> Person | None:
    """Forget a raw phone mapping while keeping historical message references."""
    person = await get_by_id(session, person_id)
    if person is None:
        return None
    person.phone = None
    if _digits(person.display_name) and not person.telegram_username:
        person.display_name = f"Saqlanmagan raqam {person.id}"
    await session.flush()
    await session.refresh(person)
    return person


async def upsert_telegram_contact(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    display_name: str,
    username: str | None = None,
    phone: str | None = None,
) -> Person:
    """Create or update a contact synced from the owner's Telegram address book.

    Matched by ``telegram_user_id``. An existing row is refreshed with the
    latest name/username/phone, but the owner flag and an already-set display
    name the owner curated are preserved: we only overwrite ``display_name``
    when the synced name is non-empty. The Telegram username is also kept as an
    alias so the contact resolves by either spelling.
    """
    person = await get_by_telegram_user_id(session, telegram_user_id)
    name = (display_name or "").strip()
    if person is None:
        return await create(
            session,
            display_name=name or (username or str(telegram_user_id)),
            telegram_user_id=telegram_user_id,
            telegram_username=username,
            phone=phone,
            aliases=[username] if username else [],
        )

    # Never let a contact sync clobber the owner's own record name.
    if name and not person.is_owner:
        person.display_name = name
    if username:
        person.telegram_username = username
        aliases = list(person.aliases or [])
        if username not in aliases:
            aliases.append(username)
            person.aliases = aliases
    if phone:
        person.phone = phone
    await session.flush()
    await session.refresh(person)
    return person


async def list_all(session: AsyncSession) -> list[Person]:
    """Return all people ordered by display name."""
    result = await session.execute(select(Person).order_by(Person.display_name))
    return list(result.scalars().all())
