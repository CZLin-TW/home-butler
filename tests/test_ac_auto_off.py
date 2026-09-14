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

    def test_manual_off_pauses_then_resumes_original_deadline(self):
        self.sync()
        manual = schedule(**{"來源": HA_MANUAL_SOURCE})
        self.sheet.rows.append(manual)
        self.sync(20)
        self.assertEqual(self.active()[0]["狀態"], "已取消")
        self.assertEqual(auto.describe(self.device, self.sheet.rows)["status"], "manual_priority")
        manual["狀態"] = "待確認"
        self.sync(30)
        self.assertEqual(self.active()[0]["狀態"], "已取消")
        self.sheet.rows.remove(manual)
        self.sync(40)
        self.assertEqual(self.active()[0]["狀態"], "待執行")
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 12:00")

    def test_changed_hours_resets_once_and_zero_disables_even_offline(self):
        self.sync()
        self.device[auto.HOURS_COLUMN] = 2
        self.sync(30)
        self.sync(40)
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 11:30")
        self.state.update(available=False, lastPower="")
        self.device[auto.HOURS_COLUMN] = 0
        self.sync(50)
        self.assertFalse(self.active())
        self.assertEqual(auto.describe(self.device, self.sheet.rows)["status"], "disabled")

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
    def test_valid_save_uses_existing_column_and_never_controls_hardware(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_home_assistant import HomeAssistantTests, load, OWNER
        base = HomeAssistantTests()
        base.setUp()
        base.config.now_taipei = lambda: datetime(2026, 9, 14, 9, tzinfo=timezone.utc)
        device = {"名稱": "測試冷氣", "Device ID": "test-ac", "類型": "空調", "狀態": "啟用", auto.HOURS_COLUMN: 0}
        devices = Sheet([device]); devices.row_values = lambda _: devices.headers
        schedules = Sheet([])
        schedules.headers = list(schedule()) + ["建立者", "建立時間", "執行識別碼", "執行結果"]
        def context():
            ctx = Context(schedules)
            ctx.data["智能居家"] = [dict(device)]
            ctx.data["排程指令"] = [dict(r) for r in schedules.rows]
            ctx.load = Mock()
            ctx.get_worksheet = lambda n: devices if n == "智能居家" else schedules
            return ctx
        fake = SimpleNamespace(RequestContext=context, update_row_fields=update_fields,
                               append_record=lambda sheet, row: sheet.rows.append(dict(row)), ensure_columns=ensure_columns)
        with patch.dict("sys.modules", {"config": base.config}):
            auth = load("auto_write_test_auth", "auth.py")
        with patch.dict("sys.modules", {"auth": auth}):
            api = load("auto_write_test_api", "ac_auto_off_api.py")
        app = FastAPI(); app.include_router(api.router)
        client = TestClient(app)
        with patch.dict("sys.modules", {"sheets": fake, "config": base.config}), patch("ha_climate.managed", return_value=True), patch("ha_climate.status", return_value={"available":True,"lastPower":"on"}), patch("ha_climate.control") as control:
            response = client.post("/api/ac/auto-off", headers={"X-API-Key":OWNER},json={"device_name":"測試冷氣","hours":3})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["scheduled_at"],"2026-09-14 12:00")
            self.assertEqual(device[auto.HOURS_COLUMN],3)
            self.assertEqual(client.get("/api/ac/auto-off",headers={"X-API-Key":OWNER}).json()["devices"]["測試冷氣"]["hours"],3)
            response = client.post("/api/ac/auto-off", headers={"X-API-Key":OWNER},json={"device_name":"測試冷氣","hours":0})
            self.assertEqual(response.json()["status"],"disabled")
            self.assertEqual(schedules.rows[0]["狀態"],"已取消")
            control.assert_not_called()

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
