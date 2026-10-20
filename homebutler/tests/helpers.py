"""A fresh database per test: SQLite in a temporary folder, or the PostgreSQL in TEST_DATABASE_URL (CI)."""
import os
import tempfile
import unittest
import uuid

from homebutler.store import migrate
from homebutler.store.db import Store, make_engine


class StoreTest(unittest.TestCase):
    def setUp(self):
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            self.schema = "t" + uuid.uuid4().hex[:12]
            engine = make_engine(url, self.schema)
            self.addCleanup(self._drop_schema, engine)
        else:
            self.schema = "hb"
            folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
            engine = make_engine("sqlite:///" + os.path.join(folder.name, "test.db"))
        migrate.upgrade(engine, self.schema)
        self.store = Store(engine)
        self.addCleanup(self.store.close)

    def _drop_schema(self, engine):
        from sqlalchemy import text
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
