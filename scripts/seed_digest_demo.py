"""Seed sample channel posts to demo the digest locally (ranking + dedup).

For testing only: inserts a few fake posts into the database — including the
SAME text in two different channels — so you can immediately ask the bot for a
digest and see that (a) posts are ranked by engagement and (b) the duplicate
appears only once. Re-running clears the previous demo data first.

    python -m scripts.seed_digest_demo

Then message the bot: «dayjest ber» (or «eng ommabop postlarni ber»).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.base import utcnow
from app.db.engine import create_db_engine, create_sessionmaker
from app.db.models.channel import Channel, ChannelPost
from app.repositories import channel_repo

_DEMO_TG_IDS = [900001, 900002]
_DUP_TEXT = "Bugun ob-havo: kuchli yomg'ir kutilmoqda"


async def main() -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    now = utcnow()

    async with sessionmaker() as session:
        # Clear previous demo posts so the digest is fresh (undigested) each run.
        existing = await session.execute(
            select(Channel).where(Channel.tg_channel_id.in_(_DEMO_TG_IDS))
        )
        for channel in existing.scalars().all():
            await session.execute(
                delete(ChannelPost).where(ChannelPost.channel_id == channel.id)
            )

        c1 = await channel_repo.get_or_create_by_tg_id(
            session, tg_channel_id=900001, username="kanal_bir", title="Kanal Bir"
        )
        c2 = await channel_repo.get_or_create_by_tg_id(
            session, tg_channel_id=900002, username="kanal_ikki", title="Kanal Ikki"
        )

        # Highest engagement — should rank #1.
        await channel_repo.upsert_post(
            session, channel_id=c1.id, tg_message_id=101,
            posted_at=now - timedelta(hours=1),
            text="Katta yangilik: yangi mahsulot taqdimoti bo'lib o'tdi",
            views=10000, forwards=500, reactions_count=800,
        )
        # SAME text in BOTH channels (dedup demo); c1 has more engagement.
        await channel_repo.upsert_post(
            session, channel_id=c1.id, tg_message_id=102,
            posted_at=now - timedelta(hours=2),
            text=_DUP_TEXT, views=3000, forwards=100, reactions_count=200,
        )
        await channel_repo.upsert_post(
            session, channel_id=c2.id, tg_message_id=201,
            posted_at=now - timedelta(hours=2),
            text=_DUP_TEXT, views=500, forwards=10, reactions_count=20,
        )
        # Medium engagement unique post.
        await channel_repo.upsert_post(
            session, channel_id=c2.id, tg_message_id=202,
            posted_at=now - timedelta(hours=3),
            text="O'rtacha xabar: hafta yangiliklari sharhi",
            views=2000, forwards=50, reactions_count=80,
        )
        # SAME topic, DIFFERENT wording in two channels (semantic dedup demo).
        await channel_repo.upsert_post(
            session, channel_id=c1.id, tg_message_id=103,
            posted_at=now - timedelta(hours=4),
            text="Prezident bugun parlamentda nutq so'zladi",
            views=4000, forwards=150, reactions_count=300,
        )
        await channel_repo.upsert_post(
            session, channel_id=c2.id, tg_message_id=203,
            posted_at=now - timedelta(hours=4),
            text="Davlat rahbarining murojaati bo'lib o'tdi",
            views=800, forwards=20, reactions_count=40,
        )
        await session.commit()

    await engine.dispose()
    print("✅ Namuna postlar qo'shildi (2 kanal, 6 post).")
    print("Endi botga yozing: «dayjest ber» yoki «eng ommabop postlarni ber».")
    print("Kutilgan: takroriy ob-havo (matn) va prezident nutqi (ma'no) "
          "har biri faqat 1 marta.")


if __name__ == "__main__":
    asyncio.run(main())
