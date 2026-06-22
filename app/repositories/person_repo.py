"""Repository for :class:`app.db.models.person.Person`.

The single owner is the contact with ``is_owner=True``. Contact search is
case-insensitive over ``display_name``, ``telegram_username`` (nickname/@handle),
the ``aliases`` JSON list, and — for digit queries — the ``phone`` number.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.person import Person

# Minimum similarity for the typo-tolerant fallback ("Azizilo" -> "Azizillo").
_FUZZY_THRESHOLD = 0.75
_FUZZY_MAX = 8
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


def _name_variants(person: Person) -> set[str]:
    """Normalized comparison keys for a person: whole names + each word token.

    Tokenizing lets a one-word query ("akmal") match a multi-word contact
    ("Akmal Karimov"), and gives the fuzzy fallback per-word targets.
    """
    from app.brain.translit import normalize_name

    raws = [person.display_name or "", person.telegram_username or ""]
    raws += [str(alias) for alias in (person.aliases or [])]
    out: set[str] = set()
    for raw in raws:
        whole = normalize_name(raw)
        if whole:
            out.add(whole)
        for token in raw.split():
            tok = normalize_name(token)
            if tok:
                out.add(tok)
    return out


async def search_by_name(session: AsyncSession, name: str) -> list[Person]:
    """Search contacts by name, @username/nickname, alias, or phone number.

    1. If the query is mostly digits (a phone search), match it against each
       contact's digit-normalized ``phone`` (substring, so partial/last digits
       work): "+998 90 123 45 67", "901234567" or just the tail all resolve.
    2. Normalized (Cyrillic↔Latin, alphanumerics-only) substring match over each
       name/username/alias and their word tokens, so "Акмал" finds "Akmal".
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
        elif lowered and (
            lowered in (person.display_name or "").lower()
            or lowered in (person.telegram_username or "").lower()
        ):
            matches[person.id] = person
    if matches:
        return list(matches.values())

    # Fuzzy fallback: closest spellings (typos / missing letters).
    if not needle:
        return []
    scored: list[tuple[float, Person]] = []
    for person in all_people:
        best = max(
            (
                difflib.SequenceMatcher(None, needle, variant).ratio()
                for variant in _name_variants(person)
            ),
            default=0.0,
        )
        if best >= _FUZZY_THRESHOLD:
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
