"""Fake Sheets/SwitchBot and real controller; never contacts cloud or hardware."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ac_feedback as feedback
from command_result import CommandResult

NOW = 10000
CFG = {**feedback.DEFAULTS, "enabled": True, "sensor_name": "室溫"}
STATE = {"ir_temperature": 26, "last_adjusted_at": 9000, "last_sample_at": 0}
ROW = {"名稱": "空調", "Device ID": "ac-id", "類型": "空調", "狀態": "啟用", "位置": "客廳",
       "最後電源": "on", "最後模式": "冷氣", "最後溫度": 26, "最後風速": "低",
       feedback.CONFIG_COL: json.dumps(CFG), feedback.STATE_COL: json.dumps(STATE)}
SENSOR_ROW = {"名稱": "室溫", "類型": "感應器", "狀態": "啟用", "位置": "客廳", "Device ID": "sensor-id"}


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        feedback._evaluated.clear(); feedback._samples.clear(); feedback._runtime.clear()
        self.rows = copy.deepcopy([ROW, SENSOR_ROW])
        self.sensor = {"室溫": {"online": True, "last_polled_at": 9999, "current": {"temp": 28}}}
        self.writes = []
        def persist(id, fields, required_fields=()):
            self.assertEqual(set(required_fields), set(fields))
            row = next(r for r in self.rows if r["Device ID"] == id)
            row.update(fields)
            self.writes.append(copy.deepcopy(fields))
            return copy.deepcopy(row), fields
        self.sheets = SimpleNamespace(get_sheet_records=Mock(side_effect=lambda _: copy.deepcopy(self.rows)),
            update_device_state_fields=Mock(side_effect=persist), get_sheet=Mock(), ensure_columns=Mock(),
            get_device_id_by_name=lambda name, ctx: next((r["Device ID"] for r in ctx.get("智能居家") if r["名稱"] == name), None))
        self.status = SimpleNamespace(catalog_rows=lambda: copy.deepcopy(self.rows), load_catalog=Mock(), snapshot=Mock(return_value={}))
        self.api = SimpleNamespace(ac_set_all=Mock(return_value={"success": True}),
                                  AC_FAN_MAP={"auto": 1, "low": 2, "medium": 3, "high": 4})
        self.patcher = patch.dict("sys.modules", {"sheets": self.sheets, "device_status": self.status,
            "sensor_state": SimpleNamespace(snapshot=lambda **_: copy.deepcopy(self.sensor)), "switchbot_api": self.api})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.clock = patch.object(feedback.time, "time", return_value=NOW)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.boot = patch.object(feedback, "_started", 0)
        self.boot.start(); self.addCleanup(self.boot.stop)

    def decide(self, **row):
        return feedback.decide({**ROW, **row}, CFG, STATE, self.sensor, NOW)

    def test_cooling_heating_and_stable_band_keep_existing_compensation(self):
        for mode in ["冷氣", "暖氣"]:
            self.assertEqual(self.decide(最後模式=mode), ("adjusting", 25))
            self.sensor["室溫"]["current"]["temp"] = 24
            self.assertEqual(self.decide(最後模式=mode), ("adjusting", 27))
            self.sensor["室溫"]["current"]["temp"] = 28
        self.sensor["室溫"]["current"]["temp"] = 26.5
        self.assertEqual(feedback.decide(ROW, CFG, {**STATE, "ir_temperature": 24}, self.sensor, NOW), ("stable", None))

    def test_no_power_or_mode_commands_for_disabled_off_or_nonthermal(self):
        for patch_row in [{"最後電源": "off"}, {"最後電源": ""}, {"最後模式": "送風"},
                          {"最後模式": "除濕"}, {feedback.CONFIG_COL: ""}]:
            self.rows[0] = {**ROW, **patch_row}
            feedback._evaluated.clear()
            feedback.tick()
        self.api.ac_set_all.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_bounds_deadband_step_cooldown_sensor_age_and_same_sample(self):
        cases = [({**STATE, "ir_temperature": 23}, "at_limit"),
                 ({**STATE, "last_adjusted_at": NOW - 100}, "settling"),
                 ({**STATE, "last_sample_at": 9999}, "waiting_sample"),
                 ({**STATE, "blocked": True}, "unconfirmed"),
                 ({**STATE, "ir_temperature": None}, "needs_manual")]
        for state, expected in cases:
            self.assertEqual(feedback.decide(ROW, CFG, state, self.sensor, NOW), (expected, None))
        for sample in [9000, 10001]:
            self.sensor["室溫"]["last_polled_at"] = sample
            self.assertEqual(self.decide(), ("sensor_stale", None))

    def test_tick_changes_only_ir_record_and_never_target_power_mode_or_timers(self):
        feedback.tick()
        self.api.ac_set_all.assert_called_once_with("ac-id", 25, 2, 2, "on")
        self.assertEqual(self.rows[0]["最後溫度"], 26)
        self.assertEqual(self.rows[0]["最後電源"], "on")
        self.assertTrue(all(set(w) == {feedback.STATE_COL} for w in self.writes))
        self.assertTrue(json.loads(self.writes[0][feedback.STATE_COL])["blocked"])
        self.assertEqual(json.loads(self.rows[0][feedback.STATE_COL])["ir_temperature"], 25)
        self.assertFalse(json.loads(self.rows[0][feedback.STATE_COL])["blocked"])
        feedback._evaluated.clear()
        feedback.tick()
        self.api.ac_set_all.assert_called_once()

    def test_unknown_and_final_persistence_failure_remain_blocked_across_restart(self):
        for failure in ["provider", "persistence"]:
            with self.subTest(failure=failure):
                self.setUpState()
                if failure == "provider": self.api.ac_set_all.return_value = {"success": False, "uncertain": True}
                else:
                    original = self.sheets.update_device_state_fields.side_effect
                    def fail_final(id, fields, **kwargs):
                        if not json.loads(fields[feedback.STATE_COL])["blocked"]: raise TimeoutError()
                        return original(id, fields, **kwargs)
                    self.sheets.update_device_state_fields.side_effect = fail_final
                feedback.tick()
                self.assertTrue(json.loads(self.rows[0][feedback.STATE_COL])["blocked"])
                feedback._evaluated.clear(); feedback._samples.clear(); feedback._runtime.clear()
                feedback.tick()
                self.api.ac_set_all.assert_called_once()

    def setUpState(self):
        self.rows = copy.deepcopy([ROW, SENSOR_ROW])
        feedback._evaluated.clear(); feedback._samples.clear(); feedback._runtime.clear()
        self.api.ac_set_all.reset_mock(); self.api.ac_set_all.return_value = {"success": True}

    def test_pre_send_persistence_failure_never_sends_ir(self):
        self.sheets.update_device_state_fields.side_effect = TimeoutError()
        feedback.tick()
        self.api.ac_set_all.assert_not_called()

    def test_half_target_uses_integer_bounds_and_disabling_rounds_without_ir(self):
        row = {**ROW, "最後溫度": 26.5}
        for measured, sent, expected in [(29, 27, 26), (29, 24, None), (24, 29, None)]:
            self.sensor["室溫"]["current"]["temp"] = measured
            result = feedback.decide(row, CFG, {**STATE, "ir_temperature": sent}, self.sensor, NOW)
            self.assertEqual(result[1], expected)
        self.rows[0] = row
        self.rows[0][feedback.STATE_COL] = json.dumps({**STATE, "ir_temperature": 24})
        feedback.save_config("空調", {**CFG, "enabled": False})
        self.assertEqual(self.rows[0]["最後溫度"], 27)
        self.assertEqual(json.loads(self.rows[0][feedback.STATE_COL])["ir_temperature"], 24)
        self.assertEqual(self.status.load_catalog.call_args.args[0][0]["最後溫度"], 27)
        self.api.ac_set_all.assert_not_called()

    def test_settings_never_send_ir_and_preserve_unknown_block(self):
        self.rows[0][feedback.STATE_COL] = json.dumps({**STATE, "blocked": True})
        feedback.save_config("空調", {**CFG, "enabled": False})
        feedback.save_config("空調", CFG)
        self.assertTrue(json.loads(self.rows[0][feedback.STATE_COL])["blocked"])
        self.api.ac_set_all.assert_not_called()
        self.rows[1]["位置"] = "主臥"
        with self.assertRaises(ValueError): feedback.save_config("空調", CFG)

    def test_invalid_config_and_wrong_sensor_identity_fail_closed(self):
        for values in [{"enabled": "true"}, {"step": 0.5}, {"interval_min": 0}, {"min_adjust_min": 0}, {"interval_min": 0.5}, {"min_adjust_min": 0.5}, {"interval_min": 31}, {"min_adjust_min": 61}, {"max_offset": 8},
                       {"tolerance": float("nan")}, {"power": "on"}]:
            with self.assertRaises(ValueError): feedback.valid_config(values)
        self.rows[1]["位置"] = "主臥"
        feedback.tick()
        self.api.ac_set_all.assert_not_called()

    def test_one_minute_api_settings_persist_without_changing_defaults(self):
        from test_homebridge import OWNER
        cfg = {**CFG, "interval_min": 1, "min_adjust_min": 1}
        client = self.api_client()
        response = client.post("/api/ac/feedback", headers={"X-API-Key": OWNER},
            json={"device_name": "空調", "config": cfg})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(feedback.config_for(self.rows[0]), cfg)
        self.assertEqual(feedback.DEFAULTS["interval_min"], 5)
        self.assertEqual(feedback.DEFAULTS["min_adjust_min"], 10)
        self.api.ac_set_all.assert_not_called()

    def test_one_minute_respects_both_intervals_and_requires_fresh_sample(self):
        cfg = {**CFG, "interval_min": 1, "min_adjust_min": 1}
        self.rows[0][feedback.CONFIG_COL] = json.dumps(cfg)
        recent = {**STATE, "last_adjusted_at": NOW - 59}
        self.assertEqual(feedback.decide(ROW, cfg, recent, self.sensor, NOW), ("settling", None))
        self.assertEqual(feedback.decide(ROW, cfg, recent, self.sensor, NOW + 1), ("adjusting", 25))
        feedback.tick()
        self.api.ac_set_all.assert_called_once()
        self.sensor["室溫"]["last_polled_at"] = NOW + 59
        with patch.object(feedback.time, "time", return_value=NOW + 59):
            feedback.tick()
        self.api.ac_set_all.assert_called_once()
        with patch.object(feedback.time, "time", return_value=NOW + 60):
            feedback.tick()
        self.assertEqual(self.api.ac_set_all.call_count, 2)
        with patch.object(feedback.time, "time", return_value=NOW + 120):
            feedback.tick()
        self.assertEqual(self.api.ac_set_all.call_count, 2)
        self.assertEqual(feedback._runtime["ac-id"]["status"], "waiting_sample")

    def test_manual_wrapper_resets_ir_to_target_and_preserves_user_setting(self):
        self.rows[0][feedback.STATE_COL] = json.dumps({**STATE, "ir_temperature": 24})
        ctx = SimpleNamespace(get=lambda _: [copy.deepcopy(self.rows[0])])
        def core(data, ctx):
            self.assertEqual(data["temperature"], 26)
            self.assertEqual(data["mode"], "cool")
            self.assertTrue(json.loads(self.rows[0][feedback.STATE_COL])["blocked"])
            self.sheets.update_device_state_fields("ac-id", feedback.manual_saved_fields(ctx, "on", 26), required_fields=[feedback.STATE_COL])
            ctx._ac_state_saved = True
            return CommandResult.success("ok")
        result = feedback.manual_control(core)({"device_name": "空調", "power": "on"}, ctx)
        self.assertEqual(result.status, "success")
        saved = json.loads(self.rows[0][feedback.STATE_COL])
        self.assertEqual(saved["ir_temperature"], 26)
        self.assertFalse(saved["blocked"])

    def test_tick_skips_while_manual_command_holds_lock_then_sees_off(self):
        ready, release = threading.Event(), threading.Event()
        def manual_off():
            with feedback.CONTROL_LOCK:
                ready.set(); release.wait(2)
                self.rows[0]["最後電源"] = "off"
        thread = threading.Thread(target=manual_off)
        thread.start(); self.assertTrue(ready.wait(1))
        try: feedback.tick()
        finally: release.set(); thread.join(2)
        feedback.tick()
        self.api.ac_set_all.assert_not_called()

    def test_fresh_sheet_overrides_old_catalog_and_restart_grace(self):
        self.status.catalog_rows = lambda: [copy.deepcopy(ROW)]
        self.rows[0]["最後電源"] = "off"
        feedback.tick()
        self.api.ac_set_all.assert_not_called()
        feedback._evaluated.clear(); self.rows[0]["最後電源"] = "on"
        with patch.object(feedback, "_started", NOW - 30): feedback.tick()
        self.api.ac_set_all.assert_not_called()

    def test_malformed_persisted_timestamps_pause_instead_of_sending(self):
        for value in [None, "invalid", -1, float("nan")]:
            self.rows[0][feedback.STATE_COL] = json.dumps({**STATE, "last_adjusted_at": value})
            feedback._evaluated.clear()
            feedback.tick()
            self.assertEqual(feedback.describe(self.rows[0], self.sensor)["status"], "unconfirmed")
        self.api.ac_set_all.assert_not_called()

    def api_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_homebridge import load, OWNER, VOICE, BRIDGE
        with patch.dict("sys.modules", {"config": SimpleNamespace(HOME_BUTLER_API_KEY=OWNER, DEVICE_VOICE_API_KEY=VOICE)}):
            auth = load("feedback_test_auth", "auth.py")
        with patch.dict("sys.modules", {"auth": auth}):
            api = load("feedback_test_api", "ac_feedback_api.py")
        app = FastAPI(); app.include_router(api.router)
        return TestClient(app)

    def test_api_rejects_bridge_voice_and_anonymous_keys_before_read_or_write(self):
        from test_homebridge import OWNER, VOICE, BRIDGE
        client = self.api_client()
        for key in ["", "invalid", BRIDGE, VOICE]:
            headers = {"X-API-Key": key}
            self.assertEqual(client.get("/api/ac/feedback", headers=headers).status_code, 401)
            self.assertEqual(client.post("/api/ac/feedback", headers=headers,
                json={"device_name": "空調", "config": CFG}).status_code, 401)
        self.sheets.get_sheet_records.assert_not_called()
        self.assertEqual(self.writes, [])
        headers = {"X-API-Key": OWNER}
        self.assertEqual(client.get("/api/ac/feedback", headers=headers).status_code, 200)
        self.assertEqual(client.post("/api/ac/feedback", headers=headers,
            json={"device_name": "空調", "config": CFG}).status_code, 200)
        self.assertEqual(client.post("/api/ac/feedback", headers=headers,
            json={"device_name": "空調", "config": {**CFG, "step": 3}}).status_code, 422)
        self.api.ac_set_all.assert_not_called()

    def test_save_and_immediate_evaluation_skip_poll_delay_and_startup_grace(self):
        from test_homebridge import OWNER
        self.rows[0].pop(feedback.STATE_COL)
        self.rows[0].pop(feedback.CONFIG_COL)
        feedback._evaluated["ac-id"] = NOW
        client = self.api_client()
        with patch.object(feedback, "_started", NOW):
            response = client.post("/api/ac/feedback", headers={"X-API-Key": OWNER},
                json={"device_name": "空調", "config": CFG, "evaluate_now": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["evaluation"]["status"], "compensating")
        self.api.ac_set_all.assert_called_once_with("ac-id", 25, 2, 2, "on")
        self.assertEqual(self.rows[0]["最後溫度"], 26)
        self.assertIn(feedback.CONFIG_COL, self.writes[0])
        # Repeated clicks and even restart cannot send twice for the same sample.
        for _ in range(2):
            feedback._evaluated.clear(); feedback._samples.clear(); feedback._runtime.clear()
            self.assertEqual(feedback.evaluate_now("空調")["status"], "waiting_sample")
        self.api.ac_set_all.assert_called_once()

    def test_evaluate_only_does_not_write_configuration_or_evaluate_other_devices(self):
        from test_homebridge import OWNER
        second = {**ROW, "名稱": "第二台", "Device ID": "other-id"}
        self.rows.append(second)
        response = self.api_client().post("/api/ac/feedback", headers={"X-API-Key": OWNER},
            json={"device_name": "空調", "evaluate_now": True})
        self.assertEqual(response.json()["evaluation"]["status"], "compensating")
        self.assertNotIn("config", response.json())
        self.assertTrue(all(set(write) == {feedback.STATE_COL} for write in self.writes))
        self.api.ac_set_all.assert_called_once_with("ac-id", 25, 2, 2, "on")
        self.assertEqual(second[feedback.STATE_COL], ROW[feedback.STATE_COL])

    def test_immediate_evaluation_retains_guards_and_actual_command_interval(self):
        cases = [({"最後電源": "off"}, "waiting_power"), ({"最後模式": "送風"}, "waiting_mode"),
                 ({"最後模式": "除濕"}, "waiting_mode"),
                 ({feedback.STATE_COL: json.dumps({**STATE, "blocked": True})}, "unconfirmed"),
                 ({feedback.STATE_COL: json.dumps({**STATE, "last_adjusted_at": NOW - 30})}, "settling")]
        for change, expected in cases:
            self.rows[0] = {**ROW, **change}
            feedback.save_config("空調", CFG)
            self.assertEqual(feedback.evaluate_now("空調")["status"], expected)
        self.rows[0] = copy.deepcopy(ROW)
        self.sensor["室溫"]["current"]["temp"] = 26.2
        self.assertEqual(feedback.evaluate_now("空調")["status"], "stable")
        self.sensor["室溫"]["last_polled_at"] = NOW - 700
        self.assertEqual(feedback.evaluate_now("空調")["status"], "sensor_stale")
        self.api.ac_set_all.assert_not_called()

    def test_first_configuration_respects_recent_manual_command_before_opt_in(self):
        from datetime import datetime, timedelta, timezone
        self.rows[0].pop(feedback.STATE_COL)
        self.rows[0]["最後更新時間"] = datetime.fromtimestamp(NOW - 30, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        feedback.save_config("空調", CFG)
        self.assertEqual(feedback.evaluate_now("空調")["status"], "settling")
        self.api.ac_set_all.assert_not_called()

    def test_save_failure_does_not_evaluate_and_unknown_send_is_not_retried(self):
        from test_homebridge import OWNER
        client = self.api_client()
        original = self.sheets.update_device_state_fields.side_effect
        self.sheets.update_device_state_fields.side_effect = TimeoutError()
        response = client.post("/api/ac/feedback", headers={"X-API-Key": OWNER},
            json={"device_name": "空調", "config": CFG, "evaluate_now": True})
        self.assertEqual(response.status_code, 503)
        self.api.ac_set_all.assert_not_called()
        self.sheets.update_device_state_fields.side_effect = original
        self.api.ac_set_all.return_value = {"success": False, "uncertain": True}
        for _ in range(2):
            self.assertEqual(feedback.evaluate_now("空調")["status"], "unconfirmed")
        self.api.ac_set_all.assert_called_once()


if __name__ == "__main__": unittest.main()
