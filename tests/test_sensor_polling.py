"""Real shared polling/state logic with fake clock, Sheets and SwitchBot."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_homebridge import load
from test_callback_concurrency import endpoint

SENSOR = {"名稱": "室溫", "Device ID": "sensor", "類型": "感應器", "狀態": "啟用", "位置": "客廳",
          "溫度補償": -1, "濕度補償": -2}
AC = {"名稱": "空調", "Device ID": "ac", "類型": "空調", "狀態": "啟用", "位置": "客廳",
      "最後電源": "on", "最後模式": "冷氣",
      "空調溫度回饋設定": json.dumps({"enabled": True, "sensor_name": "室溫"})}


class SensorPollingTests(unittest.TestCase):
    def setUp(self):
        self.now = 12000
        env = {}
        endpoint("handlers/device.py", "_parse_offset", env)
        compensate = endpoint("handlers/device.py", "apply_sensor_compensation", env)
        self.status = load("polling_device_status", "device_status.py")
        self.rows = copy.deepcopy([AC, SENSOR])
        self.status.load_catalog(self.rows)
        self.sdk = SimpleNamespace(get_hub_sensor=Mock(return_value={"temperature": 28, "humidity": 60, "co2": 800}))
        self.modules = patch.dict("sys.modules", {"gspread": SimpleNamespace(),
            "sheets": SimpleNamespace(_get_spreadsheet=Mock(side_effect=AssertionError("no Sheets"))),
            "switchbot_api": self.sdk, "device_status": self.status,
            "handlers.device": SimpleNamespace(apply_sensor_compensation=compensate)})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.state = load("polling_sensor_state", "sensor_state.py")
        patcher = patch.dict("sys.modules", {"sensor_state": self.state})
        patcher.start(); self.addCleanup(patcher.stop)
        self.poll = load("polling_module", "sensor_polling.py")
        for name in ["time", "monotonic"]:
            p = patch.object(self.poll.time, name, side_effect=lambda: self.now)
            p.start(); self.addCleanup(p.stop)
        self.append = Mock()
        self.state.threading = SimpleNamespace(Thread=lambda target, args, daemon: SimpleNamespace(start=lambda: self.append(*args)))

    def test_selection_only_active_thermal_feedback_and_shared_sensor_once(self):
        self.assertEqual(self.poll.feedback_sensors(self.rows + [{**AC, "名稱": "second", "Device ID": "ac2"}], {}), [SENSOR])
        for change in [{"最後電源": "off"}, {"最後模式": "送風"}, {"最後模式": "除濕"},
                       {"狀態": "停用"}, {"空調溫度回饋設定": "{}"}]:
            self.assertEqual(self.poll.feedback_sensors([{**AC, **change}, SENSOR], {}), [])
        for status in [{"lastPower": "off"}, {"lastMode": "送風"}, {"stateUncertain": True}]:
            self.assertEqual(self.poll.feedback_sensors(self.rows, {"空調": status}), [])
        for extra in [SENSOR, {**SENSOR, "名稱": "alias"}]:
            self.assertEqual(self.poll.feedback_sensors(self.rows + [extra], {}), [])
        self.assertEqual(self.poll.feedback_sensors([AC, {**SENSOR, "位置": "主臥"}], {}), [])

    def test_concurrent_dashboard_and_feedback_share_one_read_without_history(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.poll.refresh(SENSOR), range(8)))
        self.sdk.get_hub_sensor.assert_called_once_with("sensor")
        self.assertTrue(all(r["temperature"] == 27 for r in results))
        current = self.state.snapshot()["室溫"]
        self.assertEqual(current["current"]["humidity"], 58)
        self.assertEqual(current["current"]["co2"], 800)
        self.assertEqual(current["history"], [])
        self.assertEqual(self.status.snapshot()["室溫"]["temperature"], 27)
        self.append.assert_not_called()

    def test_failed_attempt_keeps_age_and_is_throttled_then_recovers(self):
        self.poll.refresh(SENSOR)
        self.now += 60
        self.sdk.get_hub_sensor.return_value = {"error": "offline"}
        self.assertIn("error", self.poll.refresh(SENSOR))
        self.poll.refresh(SENSOR)
        self.assertEqual(self.sdk.get_hub_sensor.call_count, 2)
        self.assertEqual(self.state.snapshot()["室溫"]["last_polled_at"], 12000)
        self.now += 60
        self.sdk.get_hub_sensor.return_value = {"temperature": 27, "humidity": 60}
        self.poll.refresh(SENSOR)
        self.assertEqual(self.state.snapshot()["室溫"]["last_polled_at"], self.now)
        self.assertEqual(self.state.snapshot()["室溫"]["current"]["temp"], 26)

    def test_minute_polling_tolerates_scheduler_jitter(self):
        self.now = 12010.1
        self.poll.refresh(SENSOR)
        self.now = 12070.0  # 59.9 seconds later, but the next scheduled minute.
        self.poll.refresh(SENSOR)
        self.poll.refresh(SENSOR)
        self.assertEqual(self.sdk.get_hub_sensor.call_count, 2)
        self.assertEqual(self.state.snapshot()["室溫"]["last_polled_at"], self.now)

    def test_live_minutes_do_not_shrink_day_history_or_increase_sheet_writes(self):
        for minute in range(1440):
            self.now = 12000 + minute * 60
            self.state.update_current("室溫", "客廳", 26 + minute / 10000, 60)
            if minute % 5 == 0:
                self.state.record_history("室溫")
                self.state.record_history("室溫")
        result = self.state.snapshot()["室溫"]
        self.assertEqual(len(result["history"]), 288)
        self.assertEqual(self.append.call_count, 288)
        self.assertEqual(result["history"][0]["t"], 12000)
        self.assertEqual(result["last_polled_at"], self.now)
        self.now += 300
        self.state.record_history("室溫")
        self.assertEqual(self.append.call_count, 288)  # Never replay stale current state.

    def test_feedback_reads_before_evaluation_and_stops_fast_polling_when_off(self):
        observed = []
        def evaluate():
            observed.append(self.state.snapshot()["室溫"]["current"]["temp"])
        import ac_feedback
        with patch.object(ac_feedback, "tick", side_effect=evaluate):
            self.poll.feedback_tick()
            self.now += 60
            self.status.update("空調", {"lastPower": "off"})
            self.poll.feedback_tick()
        self.assertEqual(observed, [27, 27])
        self.sdk.get_hub_sensor.assert_called_once()

    def test_dashboard_refresh_uses_shared_compensated_value_without_double_offset(self):
        with patch.dict("sys.modules", {"sensor_polling": self.poll}):
            fetch = endpoint("web_api.py", "_fetch_sensor_status", {})
            self.assertEqual(fetch(SENSOR), {"temperature": 27, "humidity": 58})
            self.poll.poll_feedback()
        self.sdk.get_hub_sensor.assert_called_once()

    def test_backfill_keeps_new_current_and_does_not_duplicate_history_after_restart(self):
        self.state._cached_ws = SimpleNamespace(get_all_records=lambda: [
            {"timestamp": self.now, "device_name": "室溫", "location": "客廳", "temp": 25, "humidity": 60}])
        self.state.update_current("室溫", "客廳", 27, 60)
        self.state.backfill_from_sheet()
        self.now += 30
        self.state.update_current("室溫", "客廳", 28, 60)
        self.state.record_history("室溫")
        self.assertEqual(self.state.snapshot()["室溫"]["current"]["temp"], 28)
        self.assertEqual(len(self.state.snapshot()["室溫"]["history"]), 1)
        self.append.assert_not_called()
