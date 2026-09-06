from types import SimpleNamespace
from threading import Event, Thread
import unittest
from unittest.mock import patch
from todo_access import actor_from_members, visible, select_todo
from todo_coordination import todo_write, todo_write_lock
from test_callback_concurrency import endpoint


class TodoAccessTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"待辦ID": "a", "事項": "same", "狀態": "待辦", "類型": "私人", "負責人": "Alice"},
                     {"待辦ID": "b", "事項": "same", "狀態": "待辦", "類型": "私人", "負責人": "Bob"},
                     {"待辦ID": "c", "事項": "public", "狀態": "待辦", "類型": "公開", "負責人": "Bob"}]

    def test_exact_verified_member_identity_and_disabled_member(self):
        members = [{"Line User ID": "line-a", "名稱": "Alice", "狀態": "啟用"}]
        self.assertEqual(actor_from_members(members, "line-a"), "Alice")
        for spoof in ["Alice", "line", "", "line-b"]:
            with self.assertRaises(PermissionError): actor_from_members(members, spoof)
        self.assertIsNone(actor_from_members(members, None))

    def test_no_prefix_name_match_or_private_id_access(self):
        self.assertFalse(visible(self.rows[0], "Al"))
        self.assertEqual(select_todo(self.rows, {"todo_id": "b", "person": "Bob"}, "Alice"), (None, None))
        self.assertEqual(select_todo(self.rows, {"todo_id": "a", "item": "renamed"}, "Alice")[0], 2)
        self.assertEqual(select_todo(self.rows, {"todo_id": "c"}, "Alice")[0], 4)
        self.assertEqual(select_todo(self.rows, {"item": "same"}), (None, None))

    def test_filtered_response_never_contains_other_members_private_rows(self):
        ctx = SimpleNamespace(get=lambda _: [{"Line User ID": "line-a", "名稱": "Alice", "狀態": "啟用"}])
        env = {"actor_from_members": actor_from_members, "visible": visible, "todo_write_lock": todo_write_lock,
               "refresh_todos": lambda ctx: self.rows}
        endpoint("web_api.py", "_set_actor", env)
        fn = endpoint("web_api.py", "_visible_todos", env)
        result = fn(ctx, SimpleNamespace(headers={"X-Dashboard-User": "line-a"}))
        self.assertEqual([r["待辦ID"] for r in result], ["a", "c"])

    def test_writers_refresh_after_waiting_and_do_not_overlap(self):
        entered, release, second = Event(), Event(), Event()
        live = ["old"]
        snapshots = []
        @todo_write
        def first(ctx):
            entered.set(); release.wait(2); live[0] = "new"
        @todo_write
        def other(ctx):
            snapshots.append(ctx.value); second.set()
        def refresh(ctx): ctx.value = live[0]
        with patch("todo_coordination.refresh_todos", refresh):
            a = Thread(target=first, args=(SimpleNamespace(),)); b = Thread(target=other, args=(SimpleNamespace(),))
            a.start(); self.assertTrue(entered.wait(1)); b.start()
            self.assertFalse(second.wait(0.03)); release.set(); a.join(2); b.join(2)
        self.assertEqual(snapshots, ["new"])

    def test_reentrant_completion_can_materialize_without_deadlock(self):
        @todo_write
        def inner(ctx): return "next"
        @todo_write
        def outer(ctx): return inner(ctx)
        with patch("todo_coordination.refresh_todos", lambda ctx: None):
            self.assertEqual(outer(SimpleNamespace()), "next")

    def test_line_recurring_query_does_not_leak_other_private_rules(self):
        rows = [{**r, "狀態": "啟用"} for r in self.rows]
        fn = endpoint("handlers/recurring_todo.py", "handle_query_recurring_todo", {
            "_template_sheet": lambda: SimpleNamespace(get_all_records=lambda: rows),
            "visible": visible, "format_recur_summary": lambda _: "daily"})
        result = fn(SimpleNamespace(actor_name="Alice"))
        self.assertEqual(result.count("same"), 1)
        self.assertIn("public", result)

    def test_actual_delete_handler_denies_private_id_and_keeps_notion_marker(self):
        from unittest.mock import Mock
        sheet = SimpleNamespace(get_all_values=lambda: [], delete_rows=Mock())
        ctx = SimpleNamespace(actor_name="Alice", get=lambda _: self.rows, get_worksheet=lambda _: sheet)
        update = Mock()
        fn = endpoint("handlers/todo.py", "handle_delete_todo", {
            "select_todo": select_todo, "update_row_fields": update})
        self.assertIn("❌", fn({"todo_id": "b"}, ctx))
        update.assert_not_called()
        self.rows[0].update({"來源": "Notion", "屬性": "讀寫"})
        self.assertIn("✅", fn({"todo_id": "a"}, ctx))
        update.assert_called_once_with(sheet, 2, {"狀態": "已完成"})
        sheet.delete_rows.assert_not_called()


if __name__ == "__main__": unittest.main()
