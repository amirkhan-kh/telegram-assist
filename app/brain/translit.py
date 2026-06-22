"""Uzbek Cyrillic ⇄ Latin normalization for alphabet-agnostic name matching.

Contacts may be saved in either script ("Акмал" vs "Akmal"). ``normalize_name``
folds a name to a comparable ASCII key — lowercased, Cyrillic transliterated to
Latin, digraph apostrophes and punctuation stripped — so the same person resolves
no matter which alphabet the owner (or their address book) used.
"""

from __future__ import annotations

# Uzbek Cyrillic -> Latin. Lowercase keys (we casefold before mapping).
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ў": "o", "ф": "f", "х": "x", "ҳ": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def to_latin(text: str) -> str:
    """Transliterate Uzbek Cyrillic characters to Latin (others pass through)."""
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def normalize_name(text: str) -> str:
    """Fold a name to a comparable key: lowercase, Latin, alphanumerics only.

    "Акмал" -> "akmal", "Akmal" -> "akmal", "O'tkir" -> "otkir",
    "Ўткир" -> "otkir". Spaces/apostrophes/punctuation are dropped so the key is
    robust to script and formatting differences.
    """
    folded = (text or "").casefold()
    latin = to_latin(folded)
    return "".join(ch for ch in latin if ch.isalnum())
