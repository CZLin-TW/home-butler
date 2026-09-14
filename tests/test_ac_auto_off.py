"""Persistent HA run timer tests; fake Sheets and HA, no household I/O."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import json
import ac_auto_off as auto
from schedule_execution import execute_pending, HA_MANUAL_SOURCE
from command_result import CommandResult
from test_schedule_execution import Sheet, Context, schedule, ensure_columns, update_fields
from test_callback_concurrency import endpoint


class AutoOffTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, 9, tzinfo=timezone.utc)
        self.sheet = Sheet([])
        self.sheet.headers = list(schedule()) + ["建立者", "建立時間", "執行識別碼", "執行結果"]
        self.ctx = Context(self.sheet)
        self.ctx.data["排程指令"] = []
        self.device = self.ctx.data["智能居家"][0]
        self.device[auto.HOURS_COLUMN] = 3
        self.state = {"available": True, "stateUncertain": False, "lastPower": "on"}
        self.append = Mock(side_effect=lambda sheet, row: sheet.rows.append(dict(row)))
        self.update = Mock(side_effect=update_fields)
        self.fake = SimpleNamespace(append_record=self.append, update_row_fields=self.update, ensure_columns=ensure_columns)
        self.addCleanup(patch.stopall)
        patch.dict("sys.modules", {"sheets": self.fake}).start()
        patch("ha_climate.managed", return_value=True).start()
        patch("ha_climate.status", side_effect=lambda _: dict(self.state)).start()

    def sync(self, minutes=0):
        self.ctx.set("排程指令", [dict(r) for r in self.sheet.rows])
        return auto.reconcile(self.ctx, self.now + timedelta(minutes=minutes))

    def active(self):
        return [r for r in self.sheet.rows if auto.keep_cycle(r)]

    def dispatch(self, outcome):
        handler = Mock(return_value=outcome)
        execute_pending(self.now + timedelta(hours=3), self.ctx,
                        tz=SimpleNamespace(localize=lambda d: d.replace(tzinfo=timezone.utc)),
                        handlers={"control_ac": handler}, ensure_columns=ensure_columns,
                        update_fields=update_fields, antimold_source="防黴")
        return handler

    def test_first_on_and_temperature_changes_do_not_reset_or_write(self):
        self.sync()
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 12:00")
        self.state.update(lastTemperature=29, lastMode="送風")
        with patch.object(self.ctx, "get_worksheet", side_effect=AssertionError("steady timer must not read Sheets again")):
            self.sync(60)
        self.append.assert_called_once()
        self.update.assert_not_called()
        self.assertEqual(auto.describe(self.device, self.sheet.rows)["status"], "counting")

    def test_confirmed_off_closes_cycle_next_on_starts_new_timer(self):
        self.sync()
        self.state["lastPower"] = "off"
        self.sync(30)
        self.assertEqual(self.active(), [])
        self.assertEqual(self.sheet.rows[0]["狀態"], "已取消")
        self.state["lastPower"] = "on"
        self.sync(45)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 12:45")

    def test_manual_off_does_not_hide_or_reschedule_this_cycle(self):
        self.sync()
        manual = schedule(**{"來源": HA_MANUAL_SOURCE, "觸發時間":"2026-09-15 12:00"})
        self.sheet.rows.append(manual)
        self.sync(20)
        self.assertEqual(self.active()[0]["狀態"], "待執行")
        self.state["lastPower"] = "off"
        self.sync(30)
        self.assertEqual(self.sheet.rows[0]["狀態"], "已取消")
        self.assertEqual(manual["狀態"], "待執行")

    def test_sheet_hours_changes_apply_next_cycle_and_zero_cancels_without_rearm(self):
        self.sync()
        self.device[auto.HOURS_COLUMN] = 2
        self.sync(30)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 12:00")
        self.state.update(available=False, lastPower="")
        self.device[auto.HOURS_COLUMN] = 0
        self.sync(50)
        self.assertEqual(self.active()[0]["狀態"], "已取消")
        self.state.update(available=True,lastPower="on")
        self.device[auto.HOURS_COLUMN] = 2
        self.sync(60)
        self.assertEqual(self.active()[0]["狀態"], "已取消")
        self.state["lastPower"] = "off"
        self.sync(70)
        self.state["lastPower"] = "on"
        self.sync(80)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 12:20")

    def handlers(self):
        from schedule_execution import ATTENTION_STATES, ATTEMPT_COLUMN, RESULT_COLUMN
        archive = Sheet([])
        self.sheet.delete_rows = lambda n: self.sheet.rows.pop(n-2)
        self.ctx.get_worksheet = lambda n: archive if n == "排程封存" else self.sheet
        env = {"json":json,"datetime":datetime,"HA_MANUAL_SOURCE":HA_MANUAL_SOURCE,
               "ATTENTION_STATES":ATTENTION_STATES,"ATTEMPT_COLUMN":ATTEMPT_COLUMN,"RESULT_COLUMN":RESULT_COLUMN,
               "update_row_fields":self.update,"append_record":self.append,"ensure_columns":ensure_columns,
               "maintain_ac_auto_schedule":Mock()}
        endpoint("handlers/schedule.py","_norm_trigger",env)
        endpoint("handlers/schedule.py","_locate_schedule_rows",env)
        return (endpoint("handlers/schedule.py","handle_modify_schedule",env),
                endpoint("handlers/schedule.py","handle_delete_schedule",env), archive)

    def test_edit_survives_restart_then_off_cleans_only_its_shutdown(self):
        self.sync()
        modify, _, _ = self.handlers()
        result = modify({"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00",
                         "trigger_time_new":"2026-09-14 13:00",
                         "params_new":{"power":"off","_auto_closed":True,"_auto_hours":1}},"使用者",self.ctx)
        self.assertTrue(result.startswith("✅"),result)
        self.assertEqual(auto.metadata(self.active()[0])["_auto_hours"],3)
        self.assertNotIn("_auto_closed",auto.metadata(self.active()[0]))
        self.ctx = Context(self.sheet)
        self.ctx.data["智能居家"] = [self.device]
        self.sync(30)
        self.assertEqual(self.active()[0]["觸發時間"],"2026-09-14 13:00")
        self.sheet.rows.append(schedule(**{"來源":HA_MANUAL_SOURCE,"觸發時間":"2026-09-15 12:00"}))
        self.state["lastPower"] = "off"
        self.sync(40)
        self.assertEqual(self.sheet.rows[0]["狀態"],"已取消")
        self.assertEqual(self.sheet.rows[1]["狀態"],"待執行")
        self.state["lastPower"] = "on"
        self.sync(50)
        self.assertEqual(self.active()[0]["觸發時間"],"2026-09-14 12:50")

    def test_delete_is_hidden_and_durable_until_next_off_on(self):
        self.sync()
        _, delete, _ = self.handlers()
        result = delete({"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00"},self.ctx)
        self.assertTrue(result.startswith("✅"),result)
        self.ctx = Context(self.sheet)
        self.ctx.data["智能居家"] = [self.device]
        self.sync(30)
        self.assertEqual(len(self.active()),1)
        self.assertEqual(self.active()[0]["狀態"],"已取消")
        self.dispatch(CommandResult.success("off")).assert_not_called()
        self.state["lastPower"] = "off"
        self.sync(40)
        self.state["lastPower"] = "on"
        self.sync(50)
        self.assertEqual(self.active()[0]["狀態"],"待執行")

    def test_edit_to_on_survives_off_as_normal_schedule(self):
        self.sync()
        modify, _, _ = self.handlers()
        result = modify({"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00",
                         "params_new":{"power":"on","temperature":28}},"使用者",self.ctx)
        self.assertTrue(result.startswith("✅"),result)
        self.state["lastPower"] = "off"
        self.sync(30)
        self.assertFalse(self.active())
        self.assertEqual(self.sheet.rows[0]["狀態"],"待執行")
        handler = self.dispatch(CommandResult.success("on"))
        handler.assert_called_once_with({"power":"on","temperature":28,"device_name":"測試冷氣"},self.ctx,from_auto_schedule=False)

    def test_claimed_or_ambiguous_row_cannot_be_edited_and_unknown_can_be_removed(self):
        self.sync()
        modify, delete, archive = self.handlers()
        self.sheet.rows[0].update(狀態="待確認",執行識別碼="claim-1",執行結果="unknown")
        result = modify({"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00",
                         "trigger_time_new":"2026-09-14 13:00"},"使用者",self.ctx)
        self.assertTrue(result.startswith("❌"))
        result = delete({"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00","execution_id":"claim-1"},self.ctx)
        self.assertTrue(result.startswith("✅"),result)
        self.assertEqual(archive.rows[0]["狀態"],"待確認")
        self.sync(30)
        self.assertEqual(self.active()[0]["狀態"],"已取消")

    def test_old_paused_cycle_migrates_without_changing_deadline(self):
        self.sync()
        self.sheet.rows[0].update(狀態="已取消",參數=json.dumps({"power":"off","_auto_hours":3,"_auto_paused":True}))
        self.sync(30)
        self.assertEqual(self.active()[0]["狀態"],"待執行")
        self.assertEqual(self.active()[0]["觸發時間"],"2026-09-14 12:00")

    def test_offline_and_uncertain_do_not_clear_or_dispatch(self):
        self.sync()
        for patch_state in ({"available": False}, {"available": True, "stateUncertain": True}):
            self.state.update(patch_state)
            self.sync(180)
            self.assertEqual(self.active()[0]["狀態"], "待執行")
            self.dispatch(CommandResult.success("off")).assert_not_called()
        self.update.assert_not_called()

    def test_completed_unknown_failed_are_not_rearmed_by_next_tick_or_restart(self):
        for result in (CommandResult.success("off"), CommandResult.unknown("lost"), CommandResult.failed("rejected")):
            self.sheet.rows.clear()
            self.ctx.set("排程指令", [])
            self.sync()
            handler = self.dispatch(result)
            handler.assert_called_once_with({"power": "off", "device_name": "測試冷氣"}, self.ctx, from_auto_schedule=False)
            self.assertTrue(self.active()[0]["執行識別碼"])
            # A fresh request has only persisted rows, not previous memory state.
            self.ctx = Context(self.sheet)
            self.ctx.data["智能居家"] = [self.device]
            self.ctx.data["排程指令"] = [dict(r) for r in self.sheet.rows]
            self.sync(181)
            self.assertEqual(len(self.active()), 1)
            self.dispatch(result).assert_not_called()

    def test_archive_retains_active_completed_cycle_until_observed_off(self):
        self.sync()
        self.dispatch(CommandResult.success("off"))
        self.sheet.get_all_records = lambda: self.sheet.rows
        self.sheet.delete_rows = Mock()
        archive = SimpleNamespace(row_values=lambda _: self.sheet.headers, append_row=Mock())
        ctx = SimpleNamespace(get_worksheet=lambda n: self.sheet if n == "排程指令" else archive)
        fn = endpoint("notify.py", "_archive_processed_schedules", {
            "ATTEMPT_COLUMN": "執行識別碼", "RESULT_COLUMN": "執行結果", "ensure_columns": Mock(),
            "build_row": lambda headers, row: [row.get(h, "") for h in headers]})
        fn({"測試冷氣"}, ctx)
        self.sheet.delete_rows.assert_not_called()
        self.state["lastPower"] = "off"
        self.sync(181)
        self.sheet.rows.append(schedule(**{"來源": HA_MANUAL_SOURCE, "觸發時間": "2026-09-15 20:00"}))
        fn({"測試冷氣"}, ctx)
        self.sheet.delete_rows.assert_called_once_with(2)

    def test_duplicate_cycles_and_malformed_hours_fail_closed(self):
        self.sync()
        self.sheet.rows.append(dict(self.sheet.rows[0]))
        with self.assertRaises(ValueError):
            self.sync(1)
        for value in ("bad", -1, 1.5, "nan", "inf", 169):
            self.assertEqual(auto.hours_for({auto.HOURS_COLUMN: value}), 0)


class AutoOffApiTests(unittest.TestCase):
    def test_retired_settings_post_does_not_read_or_write_sheets(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_home_assistant import HomeAssistantTests, load, OWNER
        base = HomeAssistantTests(); base.setUp()
        with patch.dict("sys.modules", {"config":base.config}):
            auth = load("auto_retired_auth", "auth.py")
        with patch.dict("sys.modules", {"auth":auth}):
            api = load("auto_retired_api", "ac_auto_off_api.py")
        app = FastAPI(); app.include_router(api.router)
        with patch.dict("sys.modules", {"sheets":SimpleNamespace()}):
            response = TestClient(app).post("/api/ac/auto-off",headers={"X-API-Key":OWNER},json={"device_name":"測試冷氣","hours":3})
        self.assertEqual(response.status_code,410)

    def test_owner_only_and_strict_hours_schema(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_home_assistant import HomeAssistantTests, load, OWNER, HA, VOICE, BRIDGE
        base = HomeAssistantTests()
        base.setUp()
        with patch.dict("sys.modules", {"config": base.config}):
            auth = load("auto_test_auth", "auth.py")
        with patch.dict("sys.modules", {"auth": auth}):
            api = load("auto_test_api", "ac_auto_off_api.py")
        app = FastAPI()
        app.include_router(api.router)
        client = TestClient(app)
        for key in ("", HA, VOICE, BRIDGE):
            self.assertEqual(client.post("/api/ac/auto-off", headers={"X-API-Key": key}, json={"device_name": "AC", "hours": 3}).status_code, 401)
        for hours in (True, "3", 0.5, -1, 169):
            self.assertEqual(client.post("/api/ac/auto-off", headers={"X-API-Key": OWNER}, json={"device_name": "AC", "hours": hours}).status_code, 422)
