"""The database connection: one engine per process, short transactions, nothing else touches SQL.

PostgreSQL keeps the tables in their own schema (settings.database_schema, "hb"), apart from the old
system's tables in "public"; SQLite has no schemas and is used as is. Every table gets row level
security on PostgreSQL (see migrations), so Supabase's public Data API can never read them.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_url(url: str) -> str:
    """Accept the plain postgres:// strings Supabase and Render hand out."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str, schema: str = "hb") -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_rules(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        return engine
    if not schema.isidentifier():
        raise ValueError("Invalid schema")
    # Prepared statements stay off for Supabase's pooler.
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"prepare_threshold": None})

    @event.listens_for(engine, "connect")
    def _our_schema_first(connection, _record):
        # A plain SET, not the "options" connection parameter: Supabase's pooler drops that silently,
        # and the tables would land in public next to the old system's.
        cursor = connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.close()
        connection.commit()
    return engine


class Store:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """One unit of work: committed when the block ends, rolled back if it raises."""
        with self._sessions() as session:
            with session.begin():
                yield session

    def close(self):
        self.engine.dispose()
