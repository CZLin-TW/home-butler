"""Daily database backup into one Drive file: content, once a day, failures and staleness. Fake Drive only."""
import gzip
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import db_backup
import history_store


class Response:
    def __init__(self, data=None, status=200):
        self.data, self.status = data or {}, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.data


class FakeDrive:
    def __init__(self, folders=1, files=1):
        self.folders, self.files, self.uploads, self.queries = folders, files, [], []

    def get(self, url, params=None, timeout=None):
        q = params["q"]
        self.queries.append(q)
        n = self.folders if "folder" in q else self.files
        return Response({"files": [{"id": f"id{i}", "name": "x"} for i in range(n)]})

    def patch(self, url, params=None, data=None, headers=None, timeout=None):
        self.uploads.append((url, data))
        return Response()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + str(Path(self.temp.name) / "store.db")
        env = patch.dict(os.environ, {"DATABASE_URL": url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DB_READS", None)
        os.environ.pop("BACKUP_FOLDER", None)
        history_store._reset_for_tests()
        self.addCleanup(history_store._reset_for_tests)
        with history_store._get_engine().begin() as conn:
            conn.execute(history_store.history.delete())
            conn.execute(history_store.system_state.delete())
        db_backup.last_error = None

    def at(self, hour, day=17):
        return datetime(2026, 10, day, hour, 0, tzinfo=db_backup.TZ)

    def test_backup_holds_every_table_but_history(self):
        history_store.append("sensor", "a", {"timestamp": time.time(), "temp": 1})
        history_store.state_set("最後每日推播日期", "2026-10-16")
        doc = json.loads(gzip.decompress(db_backup.payload(history_store._get_engine(), 123)))
        self.assertEqual(doc["format"], "homebutler-db-backup")
        self.assertNotIn("history", doc["tables"])
        self.assertEqual(doc["counts"], {"system_state": 1})
        self.assertEqual(doc["tables"]["system_state"][0]["value"], "2026-10-16")

    def test_runs_once_a_day_after_the_hour(self):
        drive = FakeDrive()
        self.assertFalse(db_backup.run_if_due(self.at(2), drive))
        self.assertTrue(db_backup.run_if_due(self.at(3), drive))
        self.assertFalse(db_backup.run_if_due(self.at(15), drive))
        self.assertTrue(db_backup.run_if_due(self.at(4, day=18), drive))
        self.assertEqual(len(drive.uploads), 2)
        self.assertTrue(drive.uploads[0][0].endswith("/id0"))
        self.assertIn("name = 'HomeButler備份'", drive.queries[0])
        self.assertEqual(db_backup.last_success(), self.at(4, day=18).timestamp())

    def test_missing_or_duplicate_targets_fail_without_marking_success(self):
        for drive in (FakeDrive(folders=0), FakeDrive(files=0), FakeDrive(files=2)):
            self.assertFalse(db_backup.run_if_due(self.at(5), drive))
            self.assertIn("expected one", db_backup.last_error)
            self.assertIsNone(db_backup.last_success())
            self.assertEqual(drive.uploads, [])

    def test_folder_name_can_be_changed_and_quotes_are_escaped(self):
        with patch.dict(os.environ, {"BACKUP_FOLDER": "Mom's backups"}):
            drive = FakeDrive()
            db_backup.run_if_due(self.at(5), drive)
        self.assertIn("name = 'Mom\\'s backups'", drive.queries[0])

    def test_a_backup_restores_into_an_emptied_database(self):
        history_store.state_set("最後每日推播日期", "2026-10-16")
        history_store.state_set("其他", "x")
        data = db_backup.payload(history_store._get_engine())
        history_store.state_set("其他", "changed")
        history_store.state_set("新的", "y")
        self.assertEqual(db_backup.restore(data), {"system_state": 2})
        os.environ["DB_READS"] = "1"
        self.assertEqual(history_store.state_get("其他"), "x")
        self.assertIsNone(history_store.state_get("新的"))
        with self.assertRaises(ValueError):
            db_backup.restore(gzip.compress(b'{"format": "other"}'))

    def test_stale_after_thirty_hours_counting_from_startup_when_never_done(self):
        with patch.object(db_backup, "_first_seen", 1000.0):
            self.assertFalse(db_backup.stale(1000 + 29 * 3600))
            self.assertTrue(db_backup.stale(1000 + 31 * 3600))
        history_store.state_set(db_backup.LAST_OK_KEY, 5000.0)
        self.assertFalse(db_backup.stale(5000 + 3600))
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertFalse(db_backup.stale(10 ** 10))
            self.assertFalse(db_backup.run_if_due(self.at(5), FakeDrive()))


if __name__ == "__main__":
    unittest.main()
