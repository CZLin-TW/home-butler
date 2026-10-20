import unittest
from datetime import datetime, timezone, timedelta

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from homebutler import modules
from homebutler.store import migrate
from homebutler.store.models import Base, Household, SystemState, utcnow
from homebutler.store.db import normalize_url
from homebutler.tests.helpers import StoreTest

modules.load()


class StoreTests(StoreTest):
    def test_versions_build_exactly_the_tables_the_code_defines(self):
        with self.store.engine.connect() as connection:
            options = {"version_table_schema": self.schema} if connection.dialect.name == "postgresql" else {}
            differences = compare_metadata(MigrationContext.configure(connection, opts=options), Base.metadata)
        # Alembic's own bookkeeping table is not part of the model (it lives in our schema on PostgreSQL).
        differences = [d for d in differences if not (d[0] == "remove_table" and d[1].name == "alembic_version")]
        self.assertEqual(differences, [])

    def test_upgrade_is_safe_to_repeat(self):
        migrate.upgrade(self.store.engine, self.schema)
        self.assertEqual(migrate.current(self.store.engine, self.schema), migrate.head())

    def test_times_come_back_as_utc_and_naive_times_are_refused(self):
        taipei = timezone(timedelta(hours=8))
        with self.store.transaction() as session:
            session.add(SystemState(key="k", value="v", updated_at=datetime(2026, 10, 21, 9, 0, tzinfo=taipei)))
        with self.store.transaction() as session:
            got = session.get(SystemState, "k").updated_at
        self.assertEqual((got.tzinfo, got.hour), (timezone.utc, 1))
        with self.assertRaises(Exception):
            with self.store.transaction() as session:
                session.add(SystemState(key="n", value="v", updated_at=datetime(2026, 10, 21)))

    def test_a_failed_transaction_leaves_nothing(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as session:
                session.add(Household(key="X", name="X"))
                raise RuntimeError("stop")
        with self.store.transaction() as session:
            self.assertIsNone(session.query(Household).filter_by(key="X").first())

    def test_row_level_security_on_every_table_in_postgres(self):
        if self.store.engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL only")
        with self.store.engine.connect() as connection:
            rows = connection.execute(text("SELECT relname, relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                           "WHERE n.nspname = :s AND c.relkind = 'r' AND relname <> 'alembic_version'"), {"s": self.schema}).all()
        self.assertTrue(rows)
        self.assertTrue(all(secure for _, secure in rows), rows)


class UrlTests(unittest.TestCase):
    def test_plain_postgres_urls_use_psycopg(self):
        self.assertEqual(normalize_url("postgres://u@h/d"), "postgresql+psycopg://u@h/d")
        self.assertEqual(normalize_url("postgresql://u@h/d"), "postgresql+psycopg://u@h/d")
        self.assertEqual(normalize_url("sqlite:///x.db"), "sqlite:///x.db")


if __name__ == "__main__":
    unittest.main()
