"""DigestService — score recent channel posts and deliver the best ones.

Channel posts are ingested by the userbot (see ``app.userbot.handlers``). This
service scores the undigested posts in a recent window by engagement
(views/forwards/reactions, weighted by the channel's importance), de-duplicates
near-identical reposts, keeps the top N, records a :class:`Digest` row so they
are not shown again, and returns an Uzbek summary.

``run`` is used two ways:
  * the scheduled ``digest`` job calls it with ``deliver=True`` to push the
    summary to the owner via the control bot;
  * the ``get_digest`` intent calls it with ``deliver=False`` and shows the
    returned summary as the bot's reply.
"""

from __future__ import annotations

import html
import json
from datetime import timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.db.base import utcnow
from app.db.models.channel import Digest
from app.integrations.gemini_client import get_gemini_client
from app.logging_conf import get_logger
from app.repositories import channel_repo, person_repo

if TYPE_CHECKING:
    from app.db.models.channel import Channel, ChannelPost
    from app.registry import ServiceRegistry

logger = get_logger(__name__)

# Engagement weights: a forward/reaction signals more interest than a passive view.
_VIEW_W = 1.0
_FORWARD_W = 3.0
_REACTION_W = 5.0


class DigestService:
    """Builds and delivers the channel digest."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    async def run(
        self,
        *,
        top_n: int | None = None,
        window_hours: int | None = None,
        deliver: bool = True,
    ) -> str | None:
        """Build the digest. Returns the Uzbek summary, or ``None`` if empty."""
        settings = self.registry.settings
        top_n = top_n or settings.digest_default_top_n
        window_hours = window_hours or settings.digest_window_hours
        since = utcnow() - timedelta(hours=window_hours)

        async with self.registry.session() as session:
            posts = await channel_repo.posts_since(session, since, only_undigested=True)
            channels = await channel_repo.channels_by_id(session)

        if not posts:
            logger.info("digest.run.empty", window_hours=window_hours)
            return None

        ranked = self._rank(posts, channels)
        # Semantic dedup costs one LLM call, so only the scheduled daily digest
        # (deliver=True) uses it; on-demand requests stay free and unlimited,
        # relying on score-ranking + exact-text dedup alone.
        if deliver:
            ranked = await self._semantic_dedup(ranked)
        ranked = ranked[:top_n]
        if not ranked:
            return None

        summary = self._format(ranked, channels)

        if deliver:
            # Only the scheduled digest persists a Digest row and marks posts as
            # seen, so the daily summary never repeats a post. On-demand views
            # (deliver=False) are read-only and can be requested repeatedly.
            async with self.registry.session() as session:
                owner = await person_repo.get_owner(session)
                if owner is not None:
                    digest = Digest(
                        owner_id=owner.id,
                        generated_at=utcnow(),
                        period_start=since,
                        period_end=utcnow(),
                        delivered=True,
                        summary_text=summary,
                    )
                    session.add(digest)
                    await session.flush()
                    await channel_repo.mark_digested(
                        session, [p.id for p in ranked], digest.id
                    )
                else:
                    logger.warning("digest.run.no_owner")
            notifier = self.registry.notification_service
            if notifier is not None:
                await notifier.notify_owner(summary, parse_mode="HTML")

        logger.info("digest.run.done", count=len(ranked), deliver=deliver)
        return summary

    # ── scoring ───────────────────────────────────────────────────────────
    @staticmethod
    def _score(post: ChannelPost, channel: Channel | None) -> float:
        """Weighted engagement score for one post."""
        views = post.views or 0
        forwards = post.forwards or 0
        reactions = post.reactions_count or 0
        base = views * _VIEW_W + forwards * _FORWARD_W + reactions * _REACTION_W
        weight = channel.weight if channel is not None else 1.0
        return base * weight

    def _rank(
        self, posts: list[ChannelPost], channels: dict[int, Channel]
    ) -> list[ChannelPost]:
        """Sort posts by score (desc), dropping near-duplicate reposts."""
        scored = sorted(
            posts,
            key=lambda p: self._score(p, channels.get(p.channel_id)),
            reverse=True,
        )
        seen: set[str] = set()
        unique: list[ChannelPost] = []
        for post in scored:
            key = (post.text or "").strip().casefold()[:120]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            unique.append(post)
        return unique

    # ── semantic dedup (LLM) ──────────────────────────────────────────────
    async def _semantic_dedup(
        self, posts: list[ChannelPost]
    ) -> list[ChannelPost]:
        """Merge same-topic posts (different wording) via Gemini; keep top-scored.

        ``posts`` is assumed already sorted by score (highest first), so the
        lowest index in a topic group is the strongest post and is kept. Degrades
        to the input unchanged when disabled, with <2 posts, or on any LLM error.
        """
        settings = self.registry.settings
        if not settings.digest_semantic_dedup or len(posts) < 2:
            return posts
        client = get_gemini_client(settings)
        if client is None:
            logger.info("digest.semantic.skipped", reason="no_gemini_client")
            return posts

        candidates = posts[:25]  # cap tokens sent to the model
        listing = "\n".join(
            f"{i}: {((p.text or '').strip().replace(chr(10), ' '))[:200]}"
            for i, p in enumerate(candidates)
        )
        prompt = (
            "Quyida raqamlangan kanal postlari bor. Bir XIL voqea yoki mavzu "
            "haqidagi postlarni (so'zlari boshqacha bo'lsa ham) bitta guruhga "
            "biriktiring: bir xil voqealarga bir xil butun son 'topic' bering. "
            'FAQAT JSON qaytaring: {"items":[{"index":<post raqami>,'
            '"topic":<butun son>}]}.\n\n' + listing
        )

        try:
            from google.genai import types

            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json"
                ),
            )
            items = json.loads(response.text or "{}").get("items", [])
        except Exception as exc:  # noqa: BLE001 - degrade to the text-dedup result
            logger.warning("digest.semantic.failed", error=str(exc)[:120])
            return posts

        topics: dict[int, list[int]] = {}
        for item in items:
            try:
                idx, topic = int(item["index"]), int(item["topic"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= idx < len(candidates):
                topics.setdefault(topic, []).append(idx)

        dropped: set[int] = set()
        for indexes in topics.values():
            if len(indexes) < 2:
                continue
            winner = min(indexes)  # lowest index == highest score
            dropped.update(i for i in indexes if i != winner)

        if not dropped:
            return posts
        logger.info("digest.semantic.merged", dropped=len(dropped))
        return [p for i, p in enumerate(posts) if i not in dropped]

    # ── formatting ────────────────────────────────────────────────────────
    _MONTHS_UZ = (
        "yanvar", "fevral", "mart", "aprel", "may", "iyun",
        "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
    )

    def _format(
        self, posts: list[ChannelPost], channels: dict[int, Channel]
    ) -> str:
        """Render the digest as a clean, popularity-ranked list of title links.

        Posts arrive already sorted by engagement (most popular first); each line
        is the post's own headline, hyperlinked to the post. No like/view/emoji
        counts are shown — the ranking uses them, the reader does not need them.
        """
        header = f"📰 <b>Kanal dayjesti</b> — {self._date_label()}"
        lines = []
        for post in posts:
            channel = channels.get(post.channel_id)
            title = html.escape(self._post_title(post, channel), quote=False)
            link = self._link(post, channel)
            if link:
                lines.append(
                    f'• <a href="{html.escape(link, quote=True)}">{title}</a>'
                )
            else:
                lines.append(f"• {title}")
        return header + "\n\n" + "\n\n".join(lines)

    def _date_label(self) -> str:
        """Today's date in the owner's zone, Uzbek style: '20-iyun'."""
        tz = ZoneInfo(self.registry.settings.user_timezone)
        today = utcnow().astimezone(tz)
        return f"{today.day}-{self._MONTHS_UZ[today.month - 1]}"

    @staticmethod
    def _post_title(
        post: ChannelPost, channel: Channel | None, limit: int = 100
    ) -> str:
        """The post's headline: its first non-empty line, collapsed + truncated.

        Falls back to the channel name for a media-only post with no text, so the
        line is always a meaningful, tappable label.
        """
        text = post.text or ""
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if not first:
            first = (
                getattr(channel, "title", None)
                or getattr(channel, "username", None)
                or "Post"
            )
        flat = " ".join(first.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    @staticmethod
    def _link(post: ChannelPost, channel: Channel | None) -> str | None:
        """Build a public/private t.me link to the post."""
        if channel is None:
            return None
        username = getattr(channel, "username", None)
        if username:
            return f"https://t.me/{username}/{post.tg_message_id}"
        raw_id = getattr(channel, "tg_channel_id", None)
        if raw_id is None:
            return None
        # Private-channel deep link uses the internal id without the -100 prefix.
        internal = str(raw_id).removeprefix("-100").lstrip("-")
        return f"https://t.me/c/{internal}/{post.tg_message_id}"
