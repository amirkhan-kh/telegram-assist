"""Channel digest tests — scoring, ordering, de-dup, and not-shown-twice."""

from __future__ import annotations

from datetime import timedelta

from app.brain.intent_router import RoutedIntent
from app.brain.intents import GetDigest
from app.db.base import utcnow
from app.repositories import channel_repo
from app.services.dispatcher import dispatch


async def _seed(registry):
    """Two channels, three recent posts with differing engagement."""
    now = utcnow()
    async with registry.session() as session:
        news = await channel_repo.create(
            session, tg_channel_id=1001, username="news", title="News", weight=1.0
        )
        blog = await channel_repo.create(
            session, tg_channel_id=1002, username="blog", title="Blog", weight=1.0
        )
        # Low engagement
        await channel_repo.upsert_post(
            session, channel_id=news.id, tg_message_id=10,
            posted_at=now - timedelta(hours=1), text="kichik yangilik",
            views=100, forwards=1, reactions_count=2,
        )
        # High engagement -> should rank first
        await channel_repo.upsert_post(
            session, channel_id=blog.id, tg_message_id=20,
            posted_at=now - timedelta(hours=2), text="katta voqea",
            views=5000, forwards=300, reactions_count=400,
        )
        # Medium engagement
        await channel_repo.upsert_post(
            session, channel_id=news.id, tg_message_id=11,
            posted_at=now - timedelta(hours=3), text="o'rtacha xabar",
            views=1000, forwards=20, reactions_count=30,
        )


async def test_digest_ranks_by_engagement(registry):
    await _seed(registry)
    summary = await registry.digest_service.run(top_n=3, deliver=False)

    assert summary is not None
    assert "katta voqea" in summary
    # The highest-engagement post must appear before the lower ones.
    assert summary.index("katta voqea") < summary.index("o'rtacha xabar")
    assert summary.index("o'rtacha xabar") < summary.index("kichik yangilik")
    assert "https://t.me/blog/20" in summary


async def test_scheduled_digest_does_not_repeat_posts(registry):
    await _seed(registry)
    # The scheduled digest (deliver=True) marks posts as seen...
    first = await registry.digest_service.run(top_n=3, deliver=True)
    assert first is not None
    # ...so the next scheduled run finds nothing new.
    second = await registry.digest_service.run(top_n=3, deliver=True)
    assert second is None


async def test_on_demand_digest_is_repeatable(registry):
    await _seed(registry)
    # On-demand views (deliver=False) are read-only and stay repeatable.
    first = await registry.digest_service.run(top_n=3, deliver=False)
    second = await registry.digest_service.run(top_n=3, deliver=False)
    assert first is not None
    assert second == first


async def test_get_digest_intent_returns_summary(registry):
    await _seed(registry)
    routed = RoutedIntent("get_digest", GetDigest(top_n=2), {})
    result = await dispatch(registry, routed, now=utcnow())
    assert "Kanal dayjesti" in result.text
    assert "katta voqea" in result.text


async def test_get_digest_empty_is_graceful(registry):
    routed = RoutedIntent("get_digest", GetDigest(top_n=5), {})
    result = await dispatch(registry, routed, now=utcnow())
    low = result.text.lower()
    assert "material yo'q" in low and "kanal" in low


async def test_digest_title_is_a_link_with_no_counts(registry):
    """Each post headline is the hyperlink; no like/view/emoji counts are shown."""
    await _seed(registry)
    summary = await registry.digest_service.run(top_n=3, deliver=False)
    assert summary is not None
    # The post headline doubles as the clickable link to the post.
    assert '<a href="https://t.me/blog/20">katta voqea</a>' in summary
    # No engagement counts / metric emojis leak into the rendered digest.
    for marker in ("❤️", "👁", "🔁", "🥇", "Postni ochish"):
        assert marker not in summary
