"""Synthetic LINE action -> handler -> Notion reconciliation, without external I/O."""
import ast
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_callback_concurrency import endpoint, ROOT
from test_notion_reconcile import row, values
from notion_reconcile import plan_changes
from todo_access import select_todo, todo_selection_message, visible


class TodoSelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(**{"事項": "面試", "日期": "2026-09-23"}),
                     row(**{"事項": "面試", "日期": "2026-09-24", "待辦ID": "todo-b", "外部ID": "page-b"}),
                     row(**{"事項": "面試", "日期": "2026-09-25", "負責人": "Bob", "待辦ID": "secret"})]
        self.sheet = SimpleNamespace(delete_rows=Mock())
        self.ctx = SimpleNamespace(actor_name="Alice", get=lambda _: self.rows,
                                   get_worksheet=lambda _: self.sheet)
        self.write = Mock()
        self.env = {"select_todo": select_todo, "todo_selection_message": todo_selection_message,
                    "update_row_fields": self.write}
        self.complete = endpoint("handlers/todo.py", "handle_delete_todo", self.env)

    def test_schema_action_completes_only_chosen_id_and_sync_keeps_marker(self):
        tree = ast.parse((ROOT / "prompt.py").read_text())
        env = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                    {"ARG_KEY_TYPES", "ACTION_NAMES", "ACTION_SCHEMA"} for t in node.targets):
                exec(compile(ast.Module(body=[node], type_ignores=[]), "prompt.py", "exec"), env)
        allowed = env["ACTION_SCHEMA"]["properties"]["actions"]["items"]["properties"]["args"]["items"]["properties"]["key"]["enum"]
        self.assertTrue({"todo_id", "date_orig", "time_orig"}.issubset(allowed))
        endpoint("assistant.py", "_coerce_arg", env)
        flatten = endpoint("assistant.py", "_flatten_action", env)
        data = flatten({"action": "delete_todo", "args": [
            {"key": "item", "value": "面試"}, {"key": "todo_id", "value": "todo-a"}]})
        self.assertIn("✅", self.complete(data, self.ctx))
        self.write.assert_called_once_with(self.sheet, 2, {"狀態": "已完成"})
        self.sheet.delete_rows.assert_not_called()
        self.assertEqual(self.rows[1]["狀態"], "待辦")
        fetched = {"Alice": ("唯讀", [("page-a", "面試", "2026-09-23", "10:00"),
                                       ("page-b", "面試", "2026-09-24", "10:00")])}
        diff = plan_changes(values(self.rows), fetched)
        self.assertFalse(diff.additions or diff.removals or diff.updates)

    def test_ambiguous_name_lists_only_authorized_options_without_writing(self):
        reply = self.complete({"item": "面試"}, self.ctx)
        self.assertIn("2026-09-23", reply)
        self.assertIn("2026-09-24", reply)
        self.assertNotIn("2026-09-25", reply)
        self.assertNotIn("Bob", reply)
        self.assertNotIn("✅", reply)
        self.write.assert_not_called()

    def test_original_date_and_time_disambiguate_same_day(self):
        self.rows[1].update({"日期": "2026-09-23", "時間": "14:00"})
        self.assertIsNone(select_todo(self.rows, {"item": "面試", "date_orig": "2026-09-23"}, "Alice")[1])
        self.assertIn("✅", self.complete({"item": "面試", "date_orig": "2026-09-23", "time_orig": "14:00"}, self.ctx))
        self.write.assert_called_once_with(self.sheet, 3, {"狀態": "已完成"})

    def test_invalid_private_completed_and_duplicate_ids_never_fall_back(self):
        self.rows[0]["狀態"] = "已完成"
        self.rows.append(dict(self.rows[1]))
        for todo_id in ["missing", "secret", "todo-a", "todo-b"]:
            with self.subTest(todo_id=todo_id):
                self.assertIn("❌", self.complete({"todo_id": todo_id, "item": "面試"}, self.ctx))
        self.write.assert_not_called()

    def test_prompt_exposes_current_ids_only_for_visible_pending_tasks(self):
        self.rows[1]["狀態"] = "已完成"
        fn = endpoint("prompt.py", "get_current_todo", {"visible": visible})
        text = fn(self.ctx)
        self.assertIn("todo_id=todo-a", text)
        self.assertNotIn("todo-b", text)
        self.assertNotIn("secret", text)

    def test_modify_ambiguity_asks_without_writing(self):
        self.env.update(ensure_columns=Mock(), LIGHT_NOTIFY_COLUMN="燈光提醒", LIGHT_AREA_ID_COLUMN="燈光區域ID")
        fn = endpoint("handlers/todo.py", "handle_modify_todo", self.env)
        reply = fn({"item": "面試", "date": "2026-09-30"}, "Alice", self.ctx)
        self.assertIn("2026-09-23", reply)
        self.write.assert_not_called()

    def test_completion_stops_only_selected_reminder(self):
        self.rows = self.rows[:2]
        self.rows[1]["日期"] = "2026-09-23"
        self.complete({"todo_id": "todo-a"}, self.ctx)
        push = Mock()
        remind = endpoint("notify.py", "_process_todo_reminders", {
            "datetime": datetime, "TZ": SimpleNamespace(localize=lambda dt: dt),
            "_push_todo_reminder": push})
        now = datetime(2026, 9, 23, 12)
        remind(now, now.date(), self.ctx)
        self.assertEqual(push.call_count, 1)
        self.assertIn("面試", push.call_args.args[2])

    def test_pipeline_preserves_clarification_then_accepts_selected_id(self):
        import json
        from test_voice_reply import VoicePipelineTests
        run, handlers, env = VoicePipelineTests().make_pipeline(
            [{"action": "delete_todo", "item": "面試"}], "已完成！", {})
        handlers["delete_todo"] = lambda data, user, ctx: self.complete(data, ctx, user)
        reply = run("fake-user", "面試完成了", "Alice", self.ctx)
        self.assertIn("請指定", reply)
        self.assertNotIn("已完成！", reply)
        self.write.assert_not_called()
        env["ask_claude"].return_value = json.dumps({"actions": [
            {"action": "delete_todo", "todo_id": "todo-b", "item": "面試"}], "reply": "已完成"})
        run("fake-user", "24 號那筆", "Alice", self.ctx)
        self.write.assert_called_once_with(self.sheet, 3, {"狀態": "已完成"})


if __name__ == "__main__":
    unittest.main()
