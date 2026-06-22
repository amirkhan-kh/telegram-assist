"""Contact resolution — turn a spoken name into a concrete person/chat.

The dispatcher needs to map free-form names ("Akmal", "opam") to a ``Person``
row and, where possible, a Telegram chat id. This module wraps
``person_repo.search_by_name`` and classifies the outcome into one of three
shapes the dispatcher can branch on:

  * a single :class:`ContactMatch` (confident pick),
  * a :class:`Disambiguation` (several plausible people — ask the owner), or
  * ``None`` (no one matched — caller may create a lightweight person).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.translit import normalize_name
from app.db.models.enums import SendMode
from app.repositories import person_repo


@dataclass
class ContactMatch:
    """A resolved contact, ready for sending/scheduling."""

    person_id: int
    chat_id: int | None
    display_name: str
    honorific: str | None
    default_send_mode: SendMode
    confidence: float


@dataclass
class Disambiguation:
    """Several people matched; the owner must choose."""

    candidates: list[ContactMatch]


# Common Uzbek honorifics/relationship words the owner appends to a name
# ("Akmal aka", "Dilnoza opa"). They are stripped before matching the saved
# contact name, which rarely contains them.
_HONORIFICS = frozenset(
    {
        "aka", "akaxon", "opa", "opajon", "uka", "ukajon", "amaki", "amakivachcha",
        "xola", "xolajon", "toga", "tog'a", "togajon", "bobo", "buvi", "buvijon",
        "domla", "ustoz", "janob", "aya", "apa", "oga", "og'a", "hoja", "hojaaka",
        "singlim", "akam", "opam", "ukam",
    }
)


def _strip_honorifics(name: str) -> str:
    """Drop leading/trailing honorific tokens ("Akmal aka" -> "Akmal").

    Returns the cleaned name; if every token is an honorific (nothing left), the
    original is returned so the caller still has something to search on.
    """
    tokens = [t for t in name.strip().split() if t]
    cleaned = list(tokens)
    while cleaned and cleaned[-1].casefold().strip(".,!?") in _HONORIFICS:
        cleaned.pop()
    while cleaned and cleaned[0].casefold().strip(".,!?") in _HONORIFICS:
        cleaned.pop(0)
    return " ".join(cleaned) if cleaned else name.strip()


def _to_match(person: object, confidence: float) -> ContactMatch:
    """Build a :class:`ContactMatch` from a ``Person`` ORM row."""

    return ContactMatch(
        person_id=person.id,
        chat_id=person.telegram_user_id,
        display_name=person.display_name,
        honorific=person.honorific,
        default_send_mode=person.default_send_mode,
        confidence=confidence,
    )


async def resolve_contact(
    session: AsyncSession, name: str
) -> ContactMatch | Disambiguation | None:
    """Resolve ``name`` to a contact, a disambiguation, or nothing.

    An exact (case-insensitive) match on the display name yields a single
    high-confidence result even when other fuzzy matches exist. Otherwise: one
    candidate -> match; several -> disambiguation; none -> ``None``.
    """

    raw = (name or "").strip()
    if not raw:
        return None

    # "Akmal aka" -> "Akmal": saved contacts rarely carry the honorific.
    needle = _strip_honorifics(raw)
    people = await person_repo.search_by_name(session, needle)
    if not people and " " in needle:
        # Fall back to the first name token (owner may have said a full name
        # while the contact is saved under just one part).
        people = await person_repo.search_by_name(session, needle.split()[0])
    if not people:
        return None

    # Exact match is script-agnostic ("Акмал" == "Akmal") and token-aware: a
    # one-word query ("Asadbek") counts as an exact hit on a multi-word contact
    # ("Asadbek Karimov") when it equals the whole name OR any single word of it.
    target = normalize_name(needle)

    def _is_exact(p: object) -> bool:
        name = p.display_name or ""
        if normalize_name(name) == target:
            return True
        return any(normalize_name(tok) == target for tok in name.split())

    exact = [p for p in people if _is_exact(p)]
    if len(exact) == 1:
        return _to_match(exact[0], 1.0)
    if len(exact) > 1:
        return Disambiguation([_to_match(p, 1.0) for p in exact])

    if len(people) == 1:
        return _to_match(people[0], 0.9)

    return Disambiguation([_to_match(p, 0.6) for p in people])
