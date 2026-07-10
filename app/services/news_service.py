"""NewsService — latest Uzbek-language world+local news from several sources.

Uzbek news sites (Daryo.uz, Kun.uz…) have no usable RSS/API and render
client-side, but each has a public Telegram channel whose web preview
(``https://t.me/s/<channel>``) is stable, server-rendered HTML: every post is a
headline plus a link back to the article. We aggregate a few Uzbek-Latin news
channels, merge by recency, de-duplicate, and show hyperlinked titles with their
source. Channels are fetched in parallel; any failing channel is skipped, and a
total failure degrades to an empty list so the command shows a clear message.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen

from app.logging_conf import get_logger

logger = get_logger(__name__)

_PREVIEW_URL = "https://t.me/s/{channel}"
_UA = "Mozilla/5.0 (compatible; JoniBot/1.0)"

# Default sources: Uzbek-Latin, broad world + local coverage. Override via
# NEWS_CHANNELS (comma-separated t.me usernames).
_DEFAULT_CHANNELS = ("Daryo", "kunuz")
# Pretty source labels shown next to each headline.
_SOURCE_NAMES = {"daryo": "Daryo", "kunuz": "Kun.uz"}

# One channel post block (split on the message wrapper, then read its parts).
_WRAP_SPLIT_RE = re.compile(r'<div class="tgme_widget_message_wrap')
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
_TIME_RE = re.compile(r'datetime="([^"]+)"')
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')
_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Hosts that are NOT the news article (Telegram itself, CDNs, socials).
_SKIP_HOST_RE = re.compile(
    r"(t\.me|telegram\.|tg\.dev|cdn|fonts|googleapis|youtu|instagram|facebook|t\.co)",
    re.IGNORECASE,
)
_TITLE_MAX = 110


@dataclass(frozen=True)
class NewsItem:
    """A headline with a link to its article, the source name, and a timestamp."""

    title: str
    url: str
    source: str
    posted_at: str  # ISO 8601 (for cross-source ordering); "" when unknown


def _strip(fragment: str) -> str:
    """Drop inline tags, unescape entities, collapse whitespace to one line."""
    return " ".join(html_lib.unescape(_TAG_RE.sub("", fragment or "")).split())


def _cap(title: str) -> str:
    """Trim an over-long headline (posts with no <br> can be a whole paragraph)."""
    if len(title) <= _TITLE_MAX:
        return title
    return title[: _TITLE_MAX - 1].rstrip() + "…"


def _title_from(inner: str) -> str:
    """The headline: the first bold run, else the text before the first <br>."""
    bold = _BOLD_RE.search(inner)
    if bold:
        title = _strip(bold.group(1))
        if len(title) >= 12:
            return _cap(title)
    return _cap(_strip(_BR_SPLIT_RE.split(inner, maxsplit=1)[0]))


def _article_link(fragment: str) -> str | None:
    """The first external (non-Telegram) link — the article on the news site."""
    for match in _HREF_RE.finditer(fragment or ""):
        url = match.group(1)
        host = url.split("/")[2] if "://" in url else ""
        if not _SKIP_HOST_RE.search(host):
            return url
    return None


def parse_channel(html_text: str, channel: str) -> list[NewsItem]:
    """Parse a ``t.me/s`` preview into the channel's article headlines.

    Posts without an external article link (promos/ads) are skipped. Order is the
    page's own (oldest→newest); :func:`fetch_news` sorts across sources by time.
    """
    source = _SOURCE_NAMES.get(channel.lower(), channel)
    items: list[NewsItem] = []
    seen: set[str] = set()
    for chunk in _WRAP_SPLIT_RE.split(html_text)[1:]:
        text_match = _TEXT_RE.search(chunk)
        if text_match is None:
            continue
        inner = text_match.group(1)
        title = _title_from(inner)
        if len(title) < 12:
            continue
        url = _article_link(inner) or _article_link(chunk)
        if url is None:
            continue
        key = title.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        time_match = _TIME_RE.search(chunk)
        items.append(
            NewsItem(
                title=title,
                url=url,
                source=source,
                posted_at=time_match.group(1) if time_match else "",
            )
        )
    return items


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=12) as resp:  # noqa: S310 - fixed t.me host
        return resp.read().decode("utf-8", errors="replace")


def _fetch_channel_sync(channel: str) -> list[NewsItem]:
    return parse_channel(_get(_PREVIEW_URL.format(channel=channel)), channel)


async def fetch_news(
    channels: list[str] | tuple[str, ...] | None = None, limit: int = 10
) -> list[NewsItem]:
    """Aggregate the latest headlines across sources (newest first); ``[]`` on fail."""
    chs = [c.strip() for c in (channels or _DEFAULT_CHANNELS) if c and c.strip()]
    if not chs:
        return []
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_channel_sync, ch) for ch in chs),
        return_exceptions=True,
    )
    items: list[NewsItem] = []
    for channel, result in zip(chs, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning("news.channel.failed", channel=channel, error=str(result)[:120])
            continue
        items.extend(result)
    # Newest first across all sources (ISO timestamps sort lexicographically;
    # timeless items fall to the end).
    items.sort(key=lambda it: it.posted_at, reverse=True)
    deduped: list[NewsItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.title.lower()[:40]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[: max(1, min(limit, 20))]
