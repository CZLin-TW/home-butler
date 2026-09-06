"""Offline dispatch regressions: never import the app or contact household services."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from command_result import CommandResult
from schedule_execution import execute_pending, ATTENTION_STATES, VISIBLE_STATES, ATTEMPT_COLUMN, RESULT_COLUMN
from test_callback_concurrency import endpoint


def schedule(**changes):
    return {"設備名稱": "測試冷氣", "動作": "control_ac", "參數": '{"power":"off"}',
            "觸發時間": "2026-09-06 12:00", "狀態": "待執行", "來源": "使用者", **changes}


class Sheet:
    def __init__(self, rows):
        self.rows = rows
        self.headers = list(rows[0]) if rows else []

    def get_all_values(self):
        return [self.headers[:]] + [[r.get(h, "") for h in self.headers] for r in self.rows]


class Context:
    def __init__(self, sheet):
        self.sheet = sheet
        self.data = {"智能居家": [{"名稱": "測試冷氣", "類型": "空調", "狀態": "啟用"}]}

    def get_worksheet(self, name): return self.sheet
    def get(self, name): return self.data.get(name, [])
    def set(self, name, rows): self.data[name] = rows


def ensure_columns(sheet, names):
    sheet.headers.extend(n for n in names if n not in sheet.headers)


def update_fields(sheet, number, fields):
    sheet.rows[number - 2].update(fields)


class ScheduleExecutionTests(unittest.TestCase):
    def setUp(self):
        self.sheet = Sheet([schedule()])
        self.ctx = Context(self.sheet)
        self.handler = Mock(return_value=CommandResult.success("accepted"))
        self.update = update_fields

    def run_tick(self):
        return execute_pending(datetime(2026, 9, 6, 12, 5, tzinfo=timezone.utc), self.ctx,
                               tz=SimpleNamespace(localize=lambda d: d.replace(tzinfo=timezone.utc)),
                               handlers={"control_ac": self.handler}, ensure_columns=ensure_columns,
                               update_fields=self.update, antimold_source="防黴")

    def test_success_requires_typed_result_and_is_not_replayed(self):
        self.handler.return_value = CommandResult.success("wording contains ❌ but status is accepted")
        self.assertEqual(self.run_tick(), {"測試冷氣"})
        self.assertEqual(self.sheet.rows[0]["狀態"], "已執行")
        self.assertTrue(self.sheet.rows[0][ATTEMPT_COLUMN])
        self.run_tick()
        self.handler.assert_called_once_with({"power": "off", "device_name": "測試冷氣"}, self.ctx, from_auto_schedule=False)

    def test_failure_retains_reason_without_replay_or_success_archive(self):
        self.handler.return_value = CommandResult.failed("✅ appears in provider text; device refused")
        self.assertEqual(self.run_tick(), set())
        self.assertEqual(self.sheet.rows[0]["狀態"], "執行失敗")
        self.assertIn("device refused", self.sheet.rows[0][RESULT_COLUMN])
        self.run_tick()
        self.handler.assert_called_once()

    def test_exception_or_legacy_response_remains_unknown(self):
        for outcome in [TimeoutError("reply lost"), "✅ old untyped handler"]:
            with self.subTest(outcome=outcome):
                self.setUp()
                if isinstance(outcome, Exception): self.handler.side_effect = outcome
                else: self.handler.return_value = outcome
                self.run_tick()
                self.assertEqual(self.sheet.rows[0]["狀態"], "待確認")
                self.run_tick()
                self.handler.assert_called_once()

    def test_bad_input_and_missing_target_send_nothing(self):
        for changes in [{"參數": "oops"}, {"參數": "[]"}, {"動作": "unknown"}, {"設備名稱": "已移除設備"}]:
            with self.subTest(changes=changes):
                self.setUp()
                self.sheet.rows[0].update(changes)
                self.run_tick()
                self.assertEqual(self.sheet.rows[0]["狀態"], "執行失敗")
                self.handler.assert_not_called()

    def test_claim_write_failure_prevents_send(self):
        self.update = Mock(side_effect=OSError("sheet unavailable"))
        self.run_tick()
        self.handler.assert_not_called()
        self.assertEqual(self.sheet.rows[0]["狀態"], "待執行")

    def test_lost_result_write_does_not_replay_after_new_context(self):
        def update(sheet, number, fields):
            if fields.get("狀態") == "已執行": raise OSError("write lost")
            update_fields(sheet, number, fields)
        self.update = update
        self.run_tick()
        self.assertEqual(self.sheet.rows[0]["狀態"], "待確認")
        self.ctx = Context(self.sheet)
        self.run_tick()
        self.handler.assert_called_once()

    def test_handler_moves_rows_and_nested_tick_cannot_duplicate_command(self):
        def dispatch(*args, **kwargs):
            self.assertEqual(self.ctx.get("排程指令")[0]["狀態"], "待確認")
            self.assertEqual(self.run_tick(), set())
            self.sheet.rows.insert(0, schedule(**{"觸發時間": "2026-09-07 12:00"}))
            return CommandResult.success("accepted")
        self.handler.side_effect = dispatch
        self.run_tick()
        self.assertEqual([r["狀態"] for r in self.sheet.rows], ["待執行", "已執行"])
        self.handler.assert_called_once()

    def test_expiry_antimold_and_automatic_ac_flag(self):
        self.sheet.rows = [schedule(**{"觸發時間": "2026-09-06 09:00"}),
                           schedule(**{"觸發時間": "2026-09-06 09:00", "來源": "防黴"}),
                           schedule(**{"來源": "自動"})]
        self.run_tick()
        self.assertEqual([r["狀態"] for r in self.sheet.rows], ["已過期", "已執行", "已執行"])
        self.assertEqual([c.kwargs for c in self.handler.call_args_list],
                         [{"from_auto_schedule": False}, {"from_auto_schedule": True}])

    def test_one_failure_does_not_skip_other_due_schedules(self):
        self.sheet.rows.append(schedule(**{"觸發時間": "2026-09-06 12:01"}))
        self.handler.side_effect = [RuntimeError("interrupted"), CommandResult.success("ok")]
        self.run_tick()
        self.assertEqual([r["狀態"] for r in self.sheet.rows], ["待確認", "已執行"])

    def test_remove_attention_by_attempt_does_not_target_same_time_pending(self):
        self.sheet.rows = [schedule(), schedule(**{"狀態": "執行失敗", ATTEMPT_COLUMN: "attempt-a"})]
        ensure_columns(self.sheet, [ATTEMPT_COLUMN])
        env = {"ATTENTION_STATES": ATTENTION_STATES, "ATTEMPT_COLUMN": ATTEMPT_COLUMN, "datetime": datetime}
        endpoint("handlers/schedule.py", "_norm_trigger", env)
        locate = endpoint("handlers/schedule.py", "_locate_schedule_rows", env)
        self.assertEqual([n for n, _ in locate(self.sheet.get_all_values(), "測試冷氣", "2026-09-06 12:00", False)], [2])
        self.assertEqual([n for n, _ in locate(self.sheet.get_all_values(), "測試冷氣", "2026-09-06 12:00", False, "attempt-a")], [3])

    def test_schedule_get_preserves_legacy_default_and_opt_in_attention(self):
        rows = [schedule(**{"狀態": s}) for s in ["待執行", "執行失敗", "待確認", "已執行"]]
        ctx = SimpleNamespace(load=Mock(), get=lambda _: rows)
        fn = endpoint("web_api.py", "api_get_schedules", {"RequestContext": lambda: ctx, "VISIBLE_STATES": VISIBLE_STATES})
        self.assertEqual([r["狀態"] for r in fn()], ["待執行"])
        self.assertEqual([r["狀態"] for r in fn(True)], ["待執行", "執行失敗", "待確認"])

    def test_archive_preserves_attempt_and_never_removes_attention(self):
        rows = [schedule(**{"狀態": s, ATTEMPT_COLUMN: s, RESULT_COLUMN: "reason"})
                for s in ["已執行", "執行失敗", "待確認"]]
        live = SimpleNamespace(get_all_records=lambda: rows, delete_rows=Mock())
        archive = SimpleNamespace(row_values=lambda _: list(rows[0]), append_row=Mock())
        ctx = SimpleNamespace(get_worksheet=lambda n: live if n == "排程指令" else archive)
        fn = endpoint("notify.py", "_archive_processed_schedules", {
            "ATTEMPT_COLUMN": ATTEMPT_COLUMN, "RESULT_COLUMN": RESULT_COLUMN, "ensure_columns": Mock(),
            "build_row": lambda headers, row: [row.get(h, "") for h in headers]})
        fn({"測試冷氣"}, ctx)
        live.delete_rows.assert_called_once_with(2)
        archive.append_row.assert_called_once()
        self.assertIn("reason", archive.append_row.call_args.args[0])


if __name__ == "__main__": unittest.main()
