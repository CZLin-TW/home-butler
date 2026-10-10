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
                        update_fields=update_fields)
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

    def test_deferral_window_moves_only_new_deadlines_inside_it(self):
        def due(hour, minute, text):
            start = datetime(2026, 9, 14, hour, minute, 40)
            return auto.deferred(start + timedelta(hours=3), auto.window_for({auto.WINDOW_COLUMN: text}))
        night = "22:00-07:00"
        self.assertEqual(due(14, 0, night), datetime(2026, 9, 14, 17, 0))
        self.assertEqual(due(18, 59, night), datetime(2026, 9, 14, 21, 59))
        self.assertEqual(due(19, 0, night), datetime(2026, 9, 15, 7, 0))
        self.assertEqual(due(23, 30, night), datetime(2026, 9, 15, 7, 0))
        self.assertEqual(due(3, 0, night), datetime(2026, 9, 14, 7, 0))
        self.assertEqual(due(4, 0, night), datetime(2026, 9, 14, 7, 0))
        self.assertEqual(due(5, 0, night), datetime(2026, 9, 14, 8, 0))
        # Same-day window, and equal ends as "always wait for that time".
        self.assertEqual(due(10, 30, "13:00-15:00"), datetime(2026, 9, 14, 15, 0))
        self.assertEqual(due(12, 0, "13:00-15:00"), datetime(2026, 9, 14, 15, 0))
        self.assertEqual(due(20, 0, "07:00-07:00"), datetime(2026, 9, 15, 7, 0))
        self.assertEqual(due(4, 0, " 7：00 ～ 07:00 "), datetime(2026, 9, 14, 7, 0))
        self.assertEqual(due(4, 1, "07:00-07:00"), datetime(2026, 9, 15, 7, 0))
        for text in ("", None, "22:00", "22-07", "24:00-07:00", "22:60-07:00", "22:00-07:00x", 2200, True):
            self.assertIsNone(auto.window_for({auto.WINDOW_COLUMN: text}))
            self.assertEqual(due(20, 0, text), datetime(2026, 9, 14, 23, 0))

    def test_window_applies_at_creation_and_later_sheet_edits_wait_for_next_cycle(self):
        self.device[auto.WINDOW_COLUMN] = "10:00-18:00"
        self.sync()
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 18:00")
        self.device[auto.WINDOW_COLUMN] = ""
        self.sync(30)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 18:00")
        self.update.assert_not_called()
        self.state["lastPower"] = "off"
        self.sync(40)
        self.state["lastPower"] = "on"
        self.sync(60)
        self.assertEqual(self.active()[0]["觸發時間"], "2026-09-14 13:00")

    def test_settings_report_what_the_cells_mean_and_preview_matches_the_real_deadline(self):
        now = datetime(2026, 9, 14, 20, 0, 30)
        def read(hours, window):
            return auto.settings({auto.HOURS_COLUMN: hours, auto.WINDOW_COLUMN: window}, now)
        good = read(1, " 7：00 ～ 07:00 ")
        self.assertEqual((good["window"], good["problems"], good["preview_off_at"]),
                         ("07:00-07:00", [], "2026-09-15 07:00"))
        self.assertEqual(read(3, "")["preview_off_at"], "2026-09-14 23:00")
        self.assertEqual(read(3, None)["problems"], [])
        self.assertEqual(read("", "")["problems"], [])
        self.assertIsNone(read(0, "")["preview_off_at"])
        typo = read(1, "7點-7點")
        self.assertEqual((typo["window"], typo["window_text"], typo["problems"], typo["preview_off_at"]),
                         (None, "7點-7點", ["window_unreadable"], "2026-09-14 21:00"))
        self.assertEqual(read("一", "22:00-07:00")["problems"], ["hours_unreadable"])
        self.assertEqual(read(1.5, "x")["problems"], ["hours_unreadable", "window_unreadable"])
        self.assertEqual(read(0, "22:00-07:00")["problems"], ["window_without_hours"])
        self.assertEqual(read("", 2200)["problems"], ["window_unreadable"])
        # The preview is the deadline reconcile would write for a cycle starting now.
        self.device[auto.WINDOW_COLUMN] = "10:00-18:00"
        preview = auto.describe(self.device, [], self.now)["preview_off_at"]
        self.sync()
        self.assertEqual(self.active()[0]["觸發時間"], preview)
        self.assertEqual(auto.describe(self.device, self.sheet.rows, self.now)["scheduled_at"], preview)

    def test_command_reconciles_at_once_but_never_fails_or_waits_on_a_busy_tick(self):
        calls = []
        class Lock:
            def __init__(self, free): self.free, self.released = free, 0
            def acquire(self, timeout): return self.free
            def release(self): self.released += 1
        def build(lock):
            return endpoint("notify.py", "reconcile_ac_auto_off_now", {
                "_schedule_cycle_lock": lock, "now_taipei": lambda: self.now,
                "RequestContext": lambda: SimpleNamespace(load=lambda names: calls.append(names))})
        free = Lock(True)
        with patch.object(auto, "reconcile", side_effect=lambda ctx, now: calls.append(now)):
            self.assertTrue(build(free)())
        self.assertEqual(calls, [["智能居家", "排程指令"], self.now])
        with patch.object(auto, "reconcile", side_effect=RuntimeError("sheets down")):
            self.assertFalse(build(free)())
        self.assertEqual(free.released, 2)
        busy = Lock(False)
        with patch.object(auto, "reconcile") as reconcile:
            self.assertFalse(build(busy)())
        reconcile.assert_not_called()
        self.assertEqual(busy.released, 0)

    def test_dashboard_command_triggers_it_only_after_a_confirmed_ha_command(self):
        class HttpError(Exception):
            def __init__(self, status_code, detail): self.status_code = status_code
        now = Mock()
        def call(result, managed=True, saved=True):
            ctx = SimpleNamespace(load=Mock(), _ac_saved_state={})
            def control(data, c):
                c._ac_state_saved = saved
                return result
            fn = endpoint("web_api.py", "api_control_ac", {
                "RequestContext": lambda: ctx, "control_ac_result": control, "HTTPException": HttpError})
            with patch.dict("sys.modules", {"ha_climate": SimpleNamespace(managed=lambda _: managed),
                                           "notify": SimpleNamespace(reconcile_ac_auto_off_now=now)}):
                return fn(SimpleNamespace(device_name="測試冷氣", power="on", temperature=None, mode=None, fan_speed=None))
        call(CommandResult.success("ok"))
        now.assert_called_once_with()
        for args in ((CommandResult.failed("no"),), (CommandResult.unknown("?"),), (CommandResult.success("ok"), True, False)):
            with self.assertRaises(HttpError):
                call(*args)
        call(CommandResult.success("ok"), managed=False)
        now.assert_called_once_with()

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

    def test_detached_job_can_be_edited_back_to_off_as_ordinary_schedule(self):
        self.sync()
        modify, _, _ = self.handlers()
        data = {"device_name":"測試冷氣","trigger_time":"2026-09-14 12:00"}
        modify({**data,"params_new":{"power":"on"}},"使用者",self.ctx)
        self.state["lastPower"] = "off"
        self.sync(30)
        result = modify({**data,"params_new":{"power":"off"}},"使用者",self.ctx)
        self.assertTrue(result.startswith("✅"),result)
        self.assertEqual(self.sheet.rows[0]["來源"],HA_MANUAL_SOURCE)
        self.assertEqual(auto.metadata(self.sheet.rows[0]),{"power":"off"})
        self.dispatch(CommandResult.success("off")).assert_called_once()

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
