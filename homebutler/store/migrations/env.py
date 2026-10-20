"""Alembic environment: always run from homebutler.store.migrate with an open connection."""
from alembic import context

from homebutler.store.models import Base

connection = context.config.attributes["connection"]
schema = context.config.attributes.get("schema")
context.configure(connection=connection, target_metadata=Base.metadata,
                  version_table_schema=schema, render_as_batch=connection.dialect.name == "sqlite")
with context.begin_transaction():
    context.run_migrations()
