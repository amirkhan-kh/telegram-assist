"""Alembic migration environment.

Uses a *synchronous* engine (settings.sync_database_url) so Alembic — which is
not async-aware — can run both offline (SQL emit) and online (live connection)
migrations. Importing ``app.db.models`` populates ``Base.metadata`` with every
table so autogenerate sees the full schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models package registers every table on Base.metadata.
import app.db.models  # noqa: F401
from app.config import get_settings
from app.db.base import Base

# Alembic Config object — provides access to the .ini values.
config = context.config

# Inject the runtime DB URL (sync DSN) from application settings so there is a
# single source of truth and no secrets in alembic.ini.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# Configure Python logging from alembic.ini (if a config file is present).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata target for 'autogenerate' support.
target_metadata = Base.metadata


def _is_sqlite() -> bool:
    url = config.get_main_option("sqlalchemy.url") or ""
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # SQLite lacks transactional DDL for many ALTERs -> use batch mode.
        render_as_batch=_is_sqlite(),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live (sync) connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=_is_sqlite(),
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
