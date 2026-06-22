"""Data-access repositories.

Each repository is a module of plain async functions whose first parameter is
``session: AsyncSession``. They use SQLAlchemy 2.0 style (``select()`` /
``session.get()`` / ``session.add()`` + ``await session.flush()``) and return
refreshed ORM objects. Transaction lifecycle is owned by the caller (the
``registry.session()`` context manager commits / rolls back).
"""
