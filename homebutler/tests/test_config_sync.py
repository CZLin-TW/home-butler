import unittest

from homebutler.domain.passwords import verify_password
from homebutler.integrations.sheets_config.source import rows_from_values
from homebutler.integrations.sheets_config.sync import MEMBERS, REMOTES, ConfigSync
from homebutler.store.models import ConfigSource, Household, Member, Remote
from homebutler.tests.helpers import StoreTest


class FakeSheets:
    def __init__(self):
        self.modified = "t1"
        self.reads = 0
        self.tabs = {
            MEMBERS: [{"名稱": "CZ", "稱謂": "爸爸", "Line User ID": "U1", "狀態": "啟用", "系統告警": "TRUE", "Notion 權限": ""},
                      {"名稱": "媽", "Line User ID": "U2", "狀態": "停用"}],
            REMOTES: [{"名稱": "平板一", "密碼": "1234", "啟用": "TRUE"}],
        }
    def modified_time(self): return self.modified
    def read(self, tabs):
        self.reads += 1
        return {tab: [dict(row) for row in self.tabs[tab]] for tab in tabs}


class ConfigSyncTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.sheets = FakeSheets()
        self.sync = ConfigSync(self.store, self.sheets, "A")

    def members(self):
        with self.store.transaction() as session:
            return {m.name: (m.active, m.removed_at is not None, m.receives_alerts, m.notion_writable, m.title)
                    for m in session.query(Member)}

    def test_members_and_remotes_arrive_and_an_unchanged_file_is_not_reread(self):
        result = self.sync.run()
        self.assertEqual(sorted(result.applied), sorted([MEMBERS, REMOTES]))
        self.assertEqual(self.members(), {"CZ": (True, False, True, False, "爸爸"), "媽": (False, False, False, False, "")})
        with self.store.transaction() as session:
            remote = session.query(Remote).one()
            self.assertTrue(remote.enabled); self.assertTrue(verify_password("1234", remote.password_hash))
            self.assertNotIn("1234", remote.password_hash)
            self.assertEqual(session.query(Household).one().key, "A")
        self.sync.run()
        self.assertEqual(self.sheets.reads, 1)                    # Google said nothing changed
        self.sheets.modified = "t2"
        self.assertEqual(self.sync.run().unchanged, [MEMBERS, REMOTES])   # changed file, same content

    def test_edits_removals_and_returns(self):
        self.sync.run()
        self.sheets.tabs[MEMBERS] = [{"名稱": "CZ", "Line User ID": "U1", "狀態": "停用"}]
        self.sheets.tabs[REMOTES][0]["密碼"] = "9999"
        self.sheets.modified = "t2"; self.sync.run()
        self.assertEqual(self.members()["CZ"][:2], (False, False))
        self.assertEqual(self.members()["媽"][:2], (False, True))       # gone from the sheet: removed, not deleted
        with self.store.transaction() as session:
            self.assertTrue(verify_password("9999", session.query(Remote).one().password_hash))
        self.sheets.tabs[MEMBERS].append({"名稱": "媽", "Line User ID": "U2", "狀態": "啟用"})
        self.sheets.modified = "t3"; self.sync.run()
        self.assertEqual(self.members()["媽"][:2], (True, False))

    def test_bad_rows_are_skipped_and_recorded_good_rows_still_apply(self):
        self.sheets.tabs[MEMBERS] += [{"名稱": "", "Line User ID": "U9"}, {"名稱": "CZ again", "Line User ID": "U1", "狀態": "啟用"}]
        self.sheets.tabs[REMOTES] += [{"名稱": "沒密碼", "啟用": "TRUE"}]
        result = self.sync.run()
        self.assertEqual(len(result.problems[MEMBERS]), 2); self.assertEqual(len(result.problems[REMOTES]), 1)
        self.assertIn("CZ", self.members())
        with self.store.transaction() as session:
            self.assertEqual(len(session.get(ConfigSource, MEMBERS).problems), 2)

    def test_a_tab_that_cannot_be_read_changes_nothing(self):
        def broken(tabs): raise LookupError("Tabs not found: 遙控器")
        self.sheets.read = broken
        with self.assertRaises(LookupError):
            self.sync.run()
        self.assertEqual(self.members(), {})
        self.sheets.read = FakeSheets().read
        self.sync.run()                                            # retried on the next run
        self.assertIn("CZ", self.members())

    def test_household_column_when_present(self):
        self.sheets.tabs[MEMBERS][1]["戶別"] = "B"
        self.sync.run()
        with self.store.transaction() as session:
            self.assertEqual({m.name: m.household.key for m in session.query(Member)}, {"CZ": "A", "媽": "B"})


class RowsTests(unittest.TestCase):
    def test_header_keyed_rows_trimmed_padded_and_blank_rows_dropped(self):
        self.assertEqual(rows_from_values([["名稱", " 密碼 ", ""], [" a ", "1"], [], ["", ""], ["b", "2", "x"]]),
                         [{"名稱": "a", "密碼": "1"}, {"名稱": "b", "密碼": "2"}])
        self.assertEqual(rows_from_values([]), [])


if __name__ == "__main__":
    unittest.main()
