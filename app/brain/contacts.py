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
    # Distinguishing details shown when several namesakes must be told apart.
    phone: str | None = None
    username: str | None = None


@dataclass
class Disambiguation:
    """Several people matched; the owner must choose."""

    candidates: list[ContactMatch]


# Common Uzbek honorifics/relationship words the owner appends to a name
# ("Akmal aka", "Dilnoza opa", "Doniyorbek og'am"). They — and their possessive
# forms ("akam", "og'am", "akasi") — are stripped before matching the saved
# contact name, which rarely carries them. Keys are matched in NORMALIZED form
# (script-agnostic, apostrophes/case folded), so "og'am" == "ogam" == "огъам".
_HONORIFIC_WORDS = {
    "aka", "akam", "akang", "akasi", "akaxon", "akajon",
    "opa", "opam", "opang", "opasi", "opajon", "apa", "apam",
    "uka", "ukam", "ukang", "ukasi", "ukajon",
    "amaki", "amakim", "amakivachcha",
    "xola", "xolam", "xolang", "xolajon",
    "toga", "tog'a", "togam", "tog'am", "togajon", "tog'ajon",
    "bobo", "bobom", "buvi", "buvim", "buvijon",
    "domla", "domlam", "ustoz", "ustozim", "janob",
    "aya", "ayam", "ona", "onam", "ota", "otam", "dada", "dadam",
    "oga", "og'a", "ogam", "og'am", "ogang", "ogasi", "og'asi", "ogajon",
    "hoja", "hojaaka", "singlim", "singil", "jiyan", "kelin", "pochcha",
    "birodar", "ogayni", "og'ayni", "doʻst", "dost", "do'st", "og'a-ini",
}

# Trailing grammatical particles ("…ga", "…ka") an owner may leave on a name
# token when typing a correction ("Doniyor aka og'am ga"). Stripped like
# honorifics. Includes the plural "lar"/"lari" used in list queries
# ("Doniyorlar kontaktlari" -> "Doniyor").
_PARTICLE_WORDS = {
    "ga", "ka", "qa", "gha", "g'a", "niki", "chi",
    "lar", "lari", "larni", "larini", "larga", "lardan", "ning", "lar's",
}

# Glued plural suffixes to peel off a single-token query ("Doniyorlar"->"Doniyor",
# "Karimovlarni"->"Karimov"). Longest first; only when a real stem (≥3) remains.
_PLURAL_SUFFIXES = ("larini", "lardan", "larga", "larni", "lari", "lar")

# Kinship honorifics that get GLUED onto a name when typed without a space
# ("Doniyoraka"->"Doniyor", "Doniyorsingil"->"Doniyor", "Doniyorog'am"->"Doniyor").
# Matched apostrophe-insensitively and only when a ≥4-char stem remains, so real
# names survive ("Malika" keeps its "ika"). Name-forming syllables that are NOT
# honorifics ("jon", "bek", "xon", "xoja") are deliberately excluded so common
# names ("Akmaljon", "Doniyorbek", "Eshonxoja") are never truncated. Longest
# first so "…akam" wins over "…aka".
_GLUED_HONORIFICS = tuple(
    sorted(
        {
            "amakivachcha", "amaki", "akaxon", "akajon", "opajon", "ukajon",
            "togajon", "ogajon", "singlim", "singil", "pochcha", "kelin", "jiyan",
            "akasi", "ukasi", "opasi", "ogasi", "togasi",
            "akam", "ukam", "opam", "ogam", "akang", "ukang", "opang", "ogang",
            "xola", "xolam", "buvi", "buvim", "bobo", "bobom", "toga", "togam",
            "amakim", "aka", "uka", "opa", "oga", "apa", "aya",
        },
        key=len,
        reverse=True,
    )
)

# Apostrophe glyphs that may sit inside a glued honorific ("og'am" vs "ogam").
_APOS = "'‘’ʻʼ`'"

_HONORIFIC_KEYS = frozenset(normalize_name(w) for w in _HONORIFIC_WORDS) - {""}
_PARTICLE_KEYS = frozenset(normalize_name(w) for w in _PARTICLE_WORDS) - {""}


def _is_strippable(token: str) -> bool:
    """True when ``token`` is a honorific/relationship word or a stray particle."""
    key = normalize_name(token)
    return bool(key) and (key in _HONORIFIC_KEYS or key in _PARTICLE_KEYS)


def _strip_honorifics(name: str) -> str:
    """Drop leading/trailing honorific/particle tokens ("Akmal aka" -> "Akmal").

    Script- and apostrophe-agnostic ("Doniyorbek og'am" -> "Doniyorbek",
    "Doniyor aka og'am ga" -> "Doniyor"). Returns the cleaned name; if every
    token is strippable (nothing left), the original is returned so the caller
    still has something to search on.
    """
    tokens = [t for t in name.strip().split() if t]
    cleaned = list(tokens)
    while cleaned and _is_strippable(cleaned[-1]):
        cleaned.pop()
    while cleaned and _is_strippable(cleaned[0]):
        cleaned.pop(0)
    if not cleaned:
        return name.strip()
    # Also peel a honorific GLUED onto the boundary tokens, so "Doniyoraka" and
    # "Doniyor aka" reduce identically (benefits both the send and lookup paths).
    cleaned[-1] = _strip_glued_honorific(cleaned[-1])
    cleaned[0] = _strip_glued_honorific(cleaned[0])
    cleaned = [t for t in cleaned if t]
    return " ".join(cleaned) if cleaned else name.strip()


def _strip_plural_suffix(token: str) -> str:
    """Peel a glued plural suffix off one token ("Doniyorlar" -> "Doniyor")."""
    low = token.casefold()
    for suffix in _PLURAL_SUFFIXES:
        if low.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: len(token) - len(suffix)]
    return token


def _strip_glued_honorific(token: str) -> str:
    """Peel a glued kinship honorific off one token, apostrophe-insensitively.

    "Doniyoraka" / "Doniyorsingil" / "Doniyorog'am" -> "Doniyor". Compares on the
    apostrophe-stripped casefold and only fires when a ≥4-char stem remains, so
    ordinary names ("Malika", "Maya") are left intact. The returned stem keeps the
    raw token's original characters (it just drops the trailing honorific).
    """
    bare = "".join(ch for ch in token.casefold() if ch not in _APOS)
    for suffix in _GLUED_HONORIFICS:
        stem_len = len(bare) - len(suffix)
        if stem_len >= 4 and bare.endswith(suffix):
            # Cut the raw token after its first ``stem_len`` non-apostrophe chars,
            # so an embedded apostrophe ("og'am") doesn't throw off the offset.
            kept = 0
            for i, ch in enumerate(token):
                if ch not in _APOS:
                    kept += 1
                    if kept == stem_len:
                        return token[: i + 1]
            return token[:stem_len]
    return token


def clean_contact_query(raw: str) -> str:
    """Normalize a free-form contact-search phrase to its bare name stem.

    Strips honorifics/particles whether SEPARATED ("Doniyor aka", "Doniyorbek
    og'am") or GLUED ("Doniyoraka", "Doniyorsingil") — via ``_strip_honorifics``,
    which now de-glues boundary tokens too — plus a glued plural ("Doniyorlar",
    "Doniyor lar" -> "Doniyor"). Returns the original (trimmed) when nothing
    strippable remains. Used by the "show me X's contacts" lookup before searching.
    """
    stripped = _strip_honorifics(raw)
    tokens = [_strip_plural_suffix(t) for t in stripped.split()]
    cleaned = " ".join(t for t in tokens if t)
    return cleaned or raw.strip()


def _name_subset_of(person: object, query: str) -> bool:
    """True when EVERY token of the contact's name appears in ``query``.

    Distinguishes a legitimate short-name hit ("Akmal Karimov" said, contact
    saved as "Akmal" — subset, accept) from a wrong namesake ("Bekzod Abdulvahob"
    said, only "Bekzod" matched a different "Bekzod Dust" — "dust" is NOT in the
    query, reject). Script-agnostic via ``normalize_name``.
    """
    q_tokens = {normalize_name(t) for t in (query or "").split()} - {""}
    p_tokens = [normalize_name(t) for t in (getattr(person, "display_name", "") or "").split()]
    p_tokens = [t for t in p_tokens if t]
    return bool(p_tokens) and all(t in q_tokens for t in p_tokens)


def _to_match(person: object, confidence: float) -> ContactMatch:
    """Build a :class:`ContactMatch` from a ``Person`` ORM row."""

    return ContactMatch(
        person_id=person.id,
        chat_id=person.telegram_user_id,
        display_name=person.display_name,
        honorific=person.honorific,
        default_send_mode=person.default_send_mode,
        confidence=confidence,
        phone=person.phone,
        username=person.telegram_username,
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
    used_first_token_fallback = False
    if not people and " " in needle:
        # Fall back to the first name token (owner may have said a full name
        # while the contact is saved under just one part).
        used_first_token_fallback = True
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
        # A single hit from a first-token fallback is risky: the owner named
        # MORE words that didn't match the full name. Trust it only when the
        # candidate's whole name is contained in what the owner said; otherwise
        # it's a different namesake ("Bekzod Abdulvahob" must NOT become "Bekzod
        # Dust") — return not-found so the owner is asked instead of mis-sent to.
        if used_first_token_fallback and not _name_subset_of(people[0], needle):
            return None
        return _to_match(people[0], 0.9)

    return Disambiguation([_to_match(p, 0.6) for p in people])
