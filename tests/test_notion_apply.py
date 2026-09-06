"""Exercise the real sync orchestration against a mutable fake Sheet (no network)."""
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
import unittest

from test_callback_concurrency import endpoint
from test_notion_reconcile import row, values
from notion_reconcile import plan_changes
from todo_coordination import todo_write_lock


class Sheet:
    def __init__(self, rows):
        self.values = values(rows)
        self.writes = []
        self.drop_append_reply = False

    def get_all_values(self): return deepcopy(self.values)

    def update(self, number, fields):
        self.writes.append(("update", number))
        for field, value in fields.items():
            self.values[number - 1][self.values[0].index(field)] = value

    def delete_rows(self, number):
        self.writes.append(("delete", number))
        del self.values[number - 1]

    def append_rows(self, rows, **kwargs):
        assert kwargs["insert_data_option"] == "INSERT_ROWS"
        self.writes.append(("append", len(rows)))
        self.values.extend(rows)
        if self.drop_append_reply:
            self.drop_append_reply = False
            raise TimeoutError("accepted, reply lost")


class SyncApplyTests(unittest.TestCase):
    def setup_sync(self, sheet, fetch):
        ctx = SimpleNamespace(get_worksheet=lambda _: sheet, set=lambda _, rows: setattr(sheet, "cached", rows))
        fn = endpoint("calendar_sync.py", "sync_external_events", {
            "_sync_lock": Lock(), "todo_write_lock": todo_write_lock,
            "_fetch_members": fetch, "ensure_columns": lambda *a: None,
            "plan_changes": plan_changes, "EXTERNAL_ID_COLUMN": "外部ID", "TODO_ID": "待辦ID",
            "new_todo_id": lambda: "new-id", "update_row_fields": lambda s, n, f: s.update(n, f),
            "build_row": lambda headers, r: [r.get(h, "") for h in headers],
            "_parse_sheet_values": lambda v: [dict(zip(v[0], r)) for r in v[1:]],
        })
        return lambda: fn(ctx)

    def test_lost_append_reply_is_reconciled_without_duplicate(self):
        sheet = Sheet([])
        fetch = lambda _: {"Alice": ("唯讀", [("page-a", "task", "2026-09-06", "10:00")])}
        sync = self.setup_sync(sheet, fetch)
        sheet.drop_append_reply = True
        with self.assertRaises(TimeoutError): sync()
        self.assertEqual(len(sheet.cached), 1)
        sync()
        self.assertEqual(sheet.writes, [("append", 1)])
        self.assertEqual(len(sheet.values), 2)

    def test_completion_during_fetch_is_seen_before_apply(self):
        sheet = Sheet([row()])
        def fetch(_):
            with todo_write_lock:
                sheet.update(2, {"狀態": "已完成"})
            return {"Alice": ("唯讀", [("page-a", "renamed", "2026-09-06", "10:00")])}
        self.setup_sync(sheet, fetch)()
        self.assertEqual(sheet.cached[0]["狀態"], "已完成")
        self.assertEqual(sheet.cached[0]["事項"], "renamed")
        self.assertEqual(sheet.cached[0]["待辦ID"], "todo-a")
        self.assertFalse(any(w[0] in ("append", "delete") for w in sheet.writes))

    def test_structural_edits_follow_updates_and_preserve_local_rows(self):
        sheet = Sheet([row(), row(**{"外部ID": "old"}), row(**{"來源": "本地", "外部ID": ""})])
        fetch = lambda _: {"Alice": ("唯讀", [("page-a", "renamed", "2026-09-06", "10:00"),
                                             ("page-b", "new", "2026-09-06", "")])}
        self.setup_sync(sheet, fetch)()
        self.assertEqual(sheet.writes, [("update", 2), ("delete", 3), ("append", 1)])
        self.assertEqual([r["來源"] for r in sheet.cached], ["Notion", "本地", "Notion"])


if __name__ == "__main__": unittest.main()
