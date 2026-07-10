"""UI — a small, consistent design language for the bot's chat replies.

Action replies render as a compact "card": an emoji + bold title, the subject in
a clean blockquote, and emoji-led detail lines (time, link, recipient…). The aim
is a modern, tidy look built from Telegram HTML + emoji instead of decorative
quotes, brackets or trailing «!» — so every reply reads professional and calm.

Telegram HTML supported: <b> <i> <u> <s> <code> <pre> <blockquote>
<blockquote expandable> <a href> <tg-spoiler>.
"""

from __future__ import annotations

import html as _html

__all__ = ["esc", "card"]


def esc(value: object) -> str:
    """HTML-escape a value for safe inline use (Uzbek apostrophes are kept)."""
    return _html.escape(str(value), quote=False)


def card(
    emoji: str,
    title: str,
    *,
    quote: str | None = None,
    fields: list[tuple[str, str]] | None = None,
    link: tuple[str, str] | None = None,
    note: str | None = None,
) -> str:
    """Build a modern reply card (Telegram HTML, parse_mode="HTML").

    Args:
        emoji, title: the header line — ``{emoji} <b>{title}</b>``.
        quote: the subject/content, shown in a blockquote (HTML-escaped).
        fields: ``(emoji, value)`` detail lines; empty values are skipped and
            each value is HTML-escaped.
        link: ``(label, url)`` rendered as ``🔗 <a href=url>label</a>``.
        note: a final, dimmer italic hint line (escaped).
    """
    lines = [f"{emoji} <b>{esc(title)}</b>"]
    if quote:
        lines.append(f"<blockquote>{esc(quote)}</blockquote>")
    for field_emoji, value in fields or []:
        if value:
            lines.append(f"{field_emoji} {esc(value)}")
    if link:
        label, url = link
        lines.append(f'🔗 <a href="{_html.escape(url, quote=True)}">{esc(label)}</a>')
    if note:
        lines.append(f"<i>{esc(note)}</i>")
    return "\n".join(lines)
