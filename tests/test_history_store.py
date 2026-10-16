"""Optional database copy of history and system state: off by default, dual-write, seed once, read back.

Runs on SQLite; set TEST_DATABASE_URL to run the same checks against PostgreSQL (CI does).
"""
import importlib.util
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import history_store

ROOT = Path(__file__).parents[1]


class Worksheet:
    def __init__(self, headers, rows=()):
        self.headers, self.rows = list(headers), [list(r) for r in rows]

    def append_row(self, row, value_input_option=None):
        self.rows.append(["" if v is None else v for v in row])

    def get_all_records(self):
        return [dict(zip(self.headers, r + [""] * len(self.headers))) for r in self.rows]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + str(Path(self.temp.name) / "store.db")
        self.env = patch.dict(os.environ, {"DATABASE_URL": url}, clear=False)
        self.env.start()
        os.environ.pop("DB_READS", None)
        self.addCleanup(self.env.stop)
        history_store._reset_for_tests()
        self.addCleanup(history_store._reset_for_tests)
        engine = history_store._get_engine()
        with engine.begin() as conn:
            conn.execute(history_store.history.delete())
            conn.execute(history_store.system_state.delete())

    def reads(self, on=True):
        if on:
            os.environ["DB_READS"] = "1"
        else:
            os.environ.pop("DB_READS", None)


class StoreTests(StoreCase):
    def test_off_without_a_connection_string(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertFalse(history_store.enabled())
            history_store.append("sensor", "x", {"timestamp": 1})
            self.assertIsNone(history_store.records("sensor", 0))
            self.assertEqual(history_store.status(), {"enabled": False})

    def test_postgres_urls_use_the_psycopg_driver(self):
        self.assertEqual(history_store._url("postgres://u@h:5432/d"), "postgresql+psycopg://u@h:5432/d")
        self.assertEqual(history_store._url("postgresql://u@h/d"), "postgresql+psycopg://u@h/d")
        self.assertEqual(history_store._url("sqlite:///x.db"), "sqlite:///x.db")

    def test_rows_are_written_always_but_read_only_when_asked(self):
        now = time.time()
        history_store.append("sensor", "Room A", {"timestamp": now - 10, "device_name": "Room A", "temp": 27.5})
        history_store.append("sensor", "x", {"timestamp": "bad"})
        history_store.append("ac", "AC-1", {"timestamp": now, "device_name": "AC-1", "power": "on"})
        self.assertIsNone(history_store.records("sensor", 0))
        self.reads()
        self.assertEqual(history_store.records("sensor", now - 3600),
                         [{"timestamp": now - 10, "device_name": "Room A", "temp": 27.5}])
        self.assertEqual(history_store.records("sensor", now), [])
        status = history_store.status()
        self.assertEqual((status["history"], status["reads"]), ({"sensor": 1, "ac": 1}, True))

    def test_seed_copies_sheet_rows_once_into_an_empty_kind(self):
        rows = [{"timestamp": 100, "device_name": "a", "temp": 1}, {"timestamp": "", "device_name": "b"},
                {"timestamp": 200, "device_name": "", "temp": 2}, {"timestamp": 300, "device_name": "a", "temp": 3}]
        self.assertEqual(history_store.seed("sensor", rows, "device_name"), 2)
        self.assertEqual(history_store.seed("sensor", rows, "device_name"), 0)
        history_store._seeded.clear()  # a restart: the table is no longer empty, so nothing is copied again
        self.assertEqual(history_store.seed("sensor", rows, "device_name"), 0)
        self.reads()
        self.assertEqual([r["temp"] for r in history_store.records("sensor", 0)], [1, 3])

    def test_old_rows_are_pruned_past_retention(self):
        with patch.object(history_store, "PRUNE_EVERY", 2):
            history_store.append("pc", "ip", {"timestamp": time.time() - 500 * 86400})
            history_store.append("pc", "ip", {"timestamp": time.time()})
        self.reads()
        self.assertEqual(len(history_store.records("pc", 0)), 1)

    def test_state_upsert_seed_and_fallback(self):
        history_store.seed_state({"最後每日推播日期": "2026-10-15", "其他": "x"})
        history_store.state_set("最後每日推播日期", "2026-10-16")
        history_store.state_set("新的鍵", "v")
        self.assertIsNone(history_store.state_get("最後每日推播日期"))  # reads still from the Sheet
        self.reads()
        self.assertEqual(history_store.state_get("最後每日推播日期"), "2026-10-16")
        self.assertEqual(history_store.state_get("其他"), "x")
        self.assertIsNone(history_store.state_get("沒有"))
        history_store._seeded.clear()
        self.reads(False)
        history_store.seed_state({"其他": "overwritten?"})
        self.reads()
        self.assertEqual(history_store.state_get("其他"), "x")

    def test_a_broken_database_never_raises(self):
        with patch.object(history_store, "_get_engine", side_effect=RuntimeError("down")):
            history_store.append("sensor", "a", {"timestamp": 1})
            self.assertEqual(history_store.seed("sensor", [{"timestamp": 1, "device_name": "a"}], "device_name"), 0)
            history_store.state_set("k", "v")
            self.reads()
            self.assertIsNone(history_store.records("sensor", 0))
            self.assertIsNone(history_store.state_get("k"))
            self.assertEqual(history_store.status()["error"], "RuntimeError")


class SensorHistoryWiringTests(StoreCase):
    """The sensor module writes both places and restores from whichever is selected."""

    def load(self, sheet):
        spec = importlib.util.spec_from_file_location("sensor_state_under_test", ROOT / "sensor_state.py")
        state = importlib.util.module_from_spec(spec)
        book = types.SimpleNamespace(worksheet=lambda _: sheet, add_worksheet=None)
        with patch.dict(sys.modules, {"gspread": types.SimpleNamespace(exceptions=types.SimpleNamespace(WorksheetNotFound=KeyError)),
                                     "sheets": types.SimpleNamespace(_get_spreadsheet=lambda: book)}):
            spec.loader.exec_module(state)
        return state

    def test_dual_write_then_restore_from_database(self):
        now = time.time()
        sheet = Worksheet(["timestamp", "device_name", "location", "temp", "humidity", "co2"],
                          [[now - 600, "Room A", "A", 26.0, 60, ""]])
        first = self.load(sheet)
        first.backfill_from_sheet()  # seeds the database with the Sheet's row
        first._sheet_append_async({"t": now - 300, "temp": 26.5, "humidity": 61, "co2": None}, "Room A", "A")
        self.assertEqual(len(sheet.rows), 2)
        self.reads()
        sheet.rows.clear()  # the database alone must be enough now
        restarted = self.load(sheet)
        restarted.backfill_from_sheet()
        history = restarted.snapshot()["Room A"]["history"]
        self.assertEqual([p["temp"] for p in history], [26.0, 26.5])

    def test_falls_back_to_the_sheet_when_the_database_fails(self):
        now = time.time()
        sheet = Worksheet(["timestamp", "device_name", "location", "temp", "humidity", "co2"],
                          [[now - 60, "Room A", "A", 25.0, 55, ""]])
        self.reads()
        state = self.load(sheet)
        with patch.object(history_store, "_get_engine", side_effect=RuntimeError("down")):
            state.backfill_from_sheet()
        self.assertEqual([p["temp"] for p in state.snapshot()["Room A"]["history"]], [25.0])


if __name__ == "__main__":
    unittest.main()
