"""Bring the database to the newest structure. Run at startup and by the migration tool; safe to repeat."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

HERE = Path(__file__).resolve().parent


def config(connection, schema=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    cfg.attributes["connection"] = connection
    cfg.attributes["schema"] = schema
    return cfg


def upgrade(engine: Engine, schema: str = "hb", revision: str = "head") -> None:
    postgres = engine.dialect.name == "postgresql"
    with engine.begin() as connection:
        if postgres:
            if not schema.isidentifier():
                raise ValueError("Invalid schema")
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        command.upgrade(config(connection, schema if postgres else None), revision)


def current(engine: Engine, schema: str = "hb") -> str | None:
    from alembic.runtime.migration import MigrationContext
    with engine.connect() as connection:
        options = {"version_table_schema": schema} if engine.dialect.name == "postgresql" else {}
        return MigrationContext.configure(connection, opts=options).get_current_revision()


def head() -> str:
    """The newest version in the code."""
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()
