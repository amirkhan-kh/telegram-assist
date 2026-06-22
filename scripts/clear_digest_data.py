"""Wipe all ingested channel data (channels, posts, digests) — local reset.

Use this to remove demo/seed data (or start the digest fresh). After running it,
restart the app: the userbot backfill re-reads the broadcast channels your
account actually follows.

    python -m scripts.clear_digest_data
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db.engine import create_db_engine, create_sessionmaker
from app.db.models.channel import Channel, ChannelPost, Digest


async def main() -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)

    async with sessionmaker() as session:
        posts = await session.scalar(select(func.count()).select_from(ChannelPost))
        channels = await session.scalar(select(func.count()).select_from(Channel))
        # Delete in FK-safe order: posts -> digests -> channels.
        await session.execute(delete(ChannelPost))
        await session.execute(delete(Digest))
        await session.execute(delete(Channel))
        await session.commit()

    await engine.dispose()
    print(f"✅ Tozalandi: {channels or 0} kanal, {posts or 0} post o'chirildi.")
    print("Endi botni qayta ishga tushiring: python -m app.main")
    print("Backfill akkauntingiz follow qilgan REAL kanallarni o'qiydi.")


if __name__ == "__main__":
    asyncio.run(main())
