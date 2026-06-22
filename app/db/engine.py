"""Async SQLAlchemy engine + session factory.

`create_db_engine` returns an `AsyncEngine`; `create_sessionmaker` returns an
`async_sessionmaker`. For sqlite we enable foreign-key enforcement and ensure
the parent directory of the db file exists.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _ensure_sqlite_dir(database_url: str) -> None:
    """Create the directory for a sqlite file db if it does not exist."""
    if "sqlite" not in database_url:
        return
    # forms: sqlite+aiosqlite:///./data/assistant.db  or  .../:memory:
    path = database_url.split(":///", 1)[-1]
    if path in ("", ":memory:") or path.startswith(":memory:"):
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def create_db_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    _ensure_sqlite_dir(database_url)
    is_sqlite = database_url.startswith("sqlite")
    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=not is_sqlite,
        future=True,
    )

    if is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def is_postgres(database_url: str) -> bool:
    return urlparse(database_url).scheme.startswith("postgresql")
