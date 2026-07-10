"""Tests for the multi-source news aggregator (get_news intent + preview parser)."""

from __future__ import annotations

from app.brain.intent_router import INTENT_MODELS
from app.brain.intents import GetNews
from app.brain.nlu_schema import NLUResult
from app.services.news_service import NewsItem, fetch_news, parse_channel

# Realistic slice of a t.me/s/<channel> preview: two article posts (each with an
# external article link + a timestamp) and one promo post (no article link).
_SAMPLE = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="Daryo/111">
    <div class="tgme_widget_message_text js-message_text"><b>Birinchi muhim yangilik
    sarlavhasi</b><br/>matn <a href="https://daryo.uz/aaa111">Batafsil</a></div>
    <a class="tgme_widget_message_date" href="https://t.me/Daryo/111">
      <time datetime="2026-06-29T08:00:00+00:00"></time></a>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="Daryo/222">
    <div class="tgme_widget_message_text js-message_text"><b>Ikkinchi yangilik
    sarlavhasi bu yerda</b><br/>tafsilot <a href="https://daryo.uz/bbb222">havola</a></div>
    <a class="tgme_widget_message_date" href="https://t.me/Daryo/222">
      <time datetime="2026-06-29T09:00:00+00:00"></time></a>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="Daryo/333">
    <div class="tgme_widget_message_text js-message_text"><b>Reklama posti shu yerda</b>
    <a href="https://t.me/Daryo/333">o'qish</a></div>
    <a class="tgme_widget_message_date" href="https://t.me/Daryo/333">
      <time datetime="2026-06-29T10:00:00+00:00"></time></a>
  </div>
</div>
"""


def test_parse_channel_extracts_title_link_source_time():
    items = parse_channel(_SAMPLE, "Daryo")
    assert len(items) == 2  # promo without an external article link is skipped
    assert items[0].title == "Birinchi muhim yangilik sarlavhasi"
    assert items[0].url == "https://daryo.uz/aaa111"
    assert items[0].source == "Daryo"
    assert items[0].posted_at == "2026-06-29T08:00:00+00:00"
    assert items[1].url == "https://daryo.uz/bbb222"


def test_known_channel_gets_pretty_source_label():
    items = parse_channel(
        _SAMPLE.replace("Daryo", "kunuz"), "kunuz"
    )
    assert items and items[0].source == "Kun.uz"


async def test_fetch_news_merges_sorts_and_dedupes(monkeypatch):
    """Two sources merge newest-first, with duplicate headlines collapsed."""
    from app.services import news_service

    def fake_channel(channel: str) -> list[NewsItem]:
        if channel == "A":
            return [
                NewsItem("Eski yangilik sarlavhasi", "https://a.uz/1", "A", "2026-06-29T07:00:00Z"),
                NewsItem("Umumiy mavzu sarlavhasi", "https://a.uz/2", "A", "2026-06-29T08:00:00Z"),
            ]
        return [
            NewsItem("Eng yangi muhim sarlavha", "https://b.uz/1", "B", "2026-06-29T10:00:00Z"),
            NewsItem("Umumiy mavzu sarlavhasi", "https://b.uz/2", "B", "2026-06-29T09:00:00Z"),
        ]

    monkeypatch.setattr(news_service, "_fetch_channel_sync", fake_channel)
    items = await fetch_news(channels=["A", "B"], limit=10)
    titles = [it.title for it in items]
    assert titles[0] == "Eng yangi muhim sarlavha"  # newest first across sources
    assert titles.count("Umumiy mavzu sarlavhasi") == 1  # duplicate collapsed


def test_get_news_intent_registered():
    assert INTENT_MODELS["get_news"] is GetNews
    result = NLUResult(
        reasoning="Owner asks for today's news.",
        intent="get_news",
        get_news=GetNews(limit=10),
    )
    assert result.get_news is not None
