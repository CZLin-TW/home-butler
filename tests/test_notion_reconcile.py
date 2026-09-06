import unittest
from notion_reconcile import plan_changes, OWNED_FIELDS


def values(rows):
    headers = [*OWNED_FIELDS, "燈光提醒", "待辦ID"]
    return [headers] + [[r.get(h, "") for h in headers] for r in rows]


def row(**overrides):
    return {"事項": "task", "日期": "2026-09-06", "時間": "10:00", "負責人": "Alice",
            "狀態": "待辦", "類型": "私人", "来源": "", "來源": "Notion", "屬性": "唯讀",
            "外部ID": "page-a", "燈光提醒": "TRUE", "待辦ID": "todo-a", **overrides}


class ReconcileTests(unittest.TestCase):
    def fetched(self, **extra):
        return {"Alice": ("唯讀", [("page-a", "task", "2026-09-06", "10:00")]), **extra}

    def test_unchanged_has_zero_writes(self):
        diff = plan_changes(values([row()]), self.fetched())
        self.assertFalse(diff.updates or diff.additions or diff.removals)

    def test_rename_changes_only_owned_fields_and_keeps_row(self):
        diff = plan_changes(values([row()]), {"Alice": ("唯讀", [("page-a", "renamed", "2026-09-07", "11:00")])})
        self.assertEqual(diff.updates, [(2, {"事項": "renamed", "日期": "2026-09-07", "時間": "11:00"})])
        self.assertFalse(diff.removals or diff.additions)

    def test_completed_page_does_not_revive_after_rename(self):
        diff = plan_changes(values([row(**{"狀態": "已完成"})]), {"Alice": ("唯讀", [("page-a", "new", "2026-09-08", "")])})
        self.assertFalse(diff.additions or diff.removals)
        self.assertNotIn("狀態", diff.updates[0][1])

    def test_failed_member_and_local_rows_are_untouched(self):
        diff = plan_changes(values([row(), row(**{"來源": "本地"}), row(**{"負責人": "Bob"})]), {"Alice": ("唯讀", [])})
        self.assertEqual(diff.removals, [2])
        self.assertFalse(diff.updates or diff.additions)

    def test_legacy_marker_gains_stable_id(self):
        diff = plan_changes(values([row(**{"外部ID": "", "狀態": "已完成"})]), self.fetched())
        self.assertEqual(diff.updates, [(2, {"外部ID": "page-a"})])
        self.assertFalse(diff.additions or diff.removals)

    def test_partial_append_retry_finds_existing_id_and_deduplicates(self):
        diff = plan_changes(values([row(), row()]), self.fetched())
        self.assertEqual(diff.removals, [3])
        self.assertFalse(diff.additions)

    def test_completion_on_failed_member_still_suppresses_shared_page(self):
        diff = plan_changes(values([row(**{"負責人": "Bob", "狀態": "已完成"}), row()]), self.fetched())
        self.assertEqual(diff.removals, [3])
        self.assertFalse(diff.additions)

    def test_new_and_removed_pages_only(self):
        diff = plan_changes(values([row()]), {"Alice": ("唯讀", [("page-b", "new", "2026-09-06", "")])})
        self.assertEqual(diff.removals, [2])
        self.assertEqual(len(diff.additions), 1)
        self.assertEqual(diff.additions[0]["外部ID"], "page-b")


if __name__ == "__main__": unittest.main()
