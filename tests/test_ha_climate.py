"""Migration boundaries use real HTTP/WebSocket code and no device/cloud I/O."""
import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_home_assistant as base
import ha_climate
HA = base.HA

AC = {"id": "c" * 32, "entity_id": "climate.living", "name": "客廳空調",
      "available": True, "hvac_mode": "cool", "temperature": 26, "fan_mode": "auto", "source_updated_at": 1000}


class HaClimateTests(unittest.TestCase):
    def test_manual_schedule_dispatch_reaches_real_ha_websocket_without_legacy(self):
        from datetime import datetime, timezone
        import ac_feedback
        from schedule_execution import execute_pending, HA_MANUAL_SOURCE
        from test_schedule_execution import Sheet, Context, schedule, ensure_columns, update_fields
        sheet = Sheet([schedule(**{"設備名稱": AC["name"], "來源": HA_MANUAL_SOURCE})])
        ctx = Context(sheet)
        ctx.data["智能居家"] = [{"名稱": AC["name"], "類型": "空調", "狀態": "啟用"}]
        legacy = Mock(side_effect=AssertionError("Must not use direct IR"))
        def tick():
            return execute_pending(datetime(2026, 9, 6, 12, 5, tzinfo=timezone.utc), ctx,
                tz=SimpleNamespace(localize=lambda d: d.replace(tzinfo=timezone.utc)),
                handlers={"control_ac": ac_feedback.manual_control(legacy)}, ensure_columns=ensure_columns,
                update_fields=update_fields, antimold_source="防黴")
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(tick)
                command = ws.receive_json()
                self.assertEqual(command["patch"], {"power": "off"})
                self.assertEqual(sheet.rows[0]["狀態"], "待確認")
                ws.send_json({"type": "climate_result", "request_id": command["request_id"],
                    "status": "success", "state": {**AC, "hvac_mode": "off"}})
                self.assertEqual(future.result(3), {AC["name"]})
                self.assertEqual(sheet.rows[0]["狀態"], "已執行")
                self.assertEqual(tick(), set())
        legacy.assert_not_called()

    def setUp(self):
        base.HomeAssistantTests.setUp(self)
        self.env = patch.dict(os.environ, {"HOME_ASSISTANT_AC_NAMES": json.dumps([AC["name"]])})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.modules = patch.dict("sys.modules", {"home_assistant_api": self.api})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def connect(self, ws):
        ws.send_json({"type": "hello", "protocol": 1, "token": HA, "climate_control": True})
        self.assertIn("climate_control", ws.receive_json()["capabilities"])
        ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "climates": [AC]})
        self.assertTrue(ws.receive_json()["accepted"])

    def test_snapshot_authority_and_disconnect_never_fall_back(self):
        row = {"名稱": AC["name"], "類型": "空調", "最後溫度": 19, "最後電源": "off"}
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            self.assertEqual(ha_climate.overlay_row(row)["最後溫度"], 26)
            self.assertTrue(ha_climate.status(AC["name"])["available"])
        self.assertTrue(ha_climate.managed(AC["name"]))
        self.assertFalse(ha_climate.status(AC["name"])["available"])
        self.assertEqual(ha_climate.overlay_row(row)["最後電源"], "")
        self.assertIsNone(ha_climate.overlay_row(row)["最後溫度"])

    def test_success_requires_matching_ha_result(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            pending = asyncio.run_coroutine_threadsafe(self.api.link.command(AC["name"], {"power": "on", "temperature": 27}), self.api.link.loop)
            command = ws.receive_json()
            self.assertEqual(command["type"], "climate_command")
            self.assertEqual(command["id"], AC["id"])
            self.assertEqual(command["patch"], {"power": "on", "temperature": 27})
            ws.send_json({"type": "climate_result", "request_id": command["request_id"],
                          "status": "success", "state": {**AC, "temperature": 27}})
            self.assertEqual(pending.result(3)["status"], "success")
            self.assertEqual(ha_climate.status(AC["name"])["lastTemperature"], 27)

    def test_unselected_and_duplicate_commands_not_dispatched(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            denied = asyncio.run_coroutine_threadsafe(self.api.link.command("別台", {"power": "off"}), self.api.link.loop)
            self.assertEqual(denied.result(3)["status"], "failed")
            first = asyncio.run_coroutine_threadsafe(self.api.link.command(AC["name"], {"power": "off"}), self.api.link.loop)
            command = ws.receive_json()
            second = asyncio.run_coroutine_threadsafe(self.api.link.command(AC["name"], {"power": "off"}), self.api.link.loop)
            self.assertEqual(second.result(3)["status"], "failed")
            ws.send_json({"type": "climate_result", "request_id": command["request_id"], "status": "failed"})
            self.assertEqual(first.result(3)["status"], "failed")

    def test_handler_rounds_integer_and_uses_ha_confirmation(self):
        row = {"名稱": AC["name"], "類型": "空調", "狀態": "啟用", "最後溫度": 19}
        ctx = SimpleNamespace(get=lambda name: [row])
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(ha_climate.control, {"device_name": AC["name"], "temperature": 26.5, "mode": "fan"}, ctx)
                frame = ws.receive_json()
                self.assertEqual(frame["patch"], {"power": "on", "temperature": 27, "mode": "fan_only"})
                ws.send_json({"type": "climate_result", "request_id": frame["request_id"], "status": "success",
                              "state": {**AC, "temperature": 27, "hvac_mode": "fan_only"}})
                self.assertEqual(pending.result(3).status, "success")
            self.assertEqual(ctx._ac_saved_state["lastTemperature"], 27)
            self.assertEqual(row["最後模式"], "送風")

    def test_handler_rejects_invalid_target_before_dispatch(self):
        ctx = SimpleNamespace(get=lambda name: [{"名稱": AC["name"], "類型": "空調", "狀態": "啟用"}])
        for value in (True, float("nan"), 31):
            self.assertEqual(ha_climate.control({"device_name": AC["name"], "temperature": value}, ctx).status, "failed")

    def test_dashboard_display_labels_reach_ha_and_return_matching_labels(self):
        # These are the actual Dashboard option values, not English-only test inputs.
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            for mode, expected_mode, fan, expected_fan in [
                ("冷氣", "cool", "低", "low"), ("除濕", "dry", "中", "medium"),
                ("送風", "fan_only", "高", "high"), ("暖氣", "heat", "自動", "auto"),
                ("自動", "heat_cool", "自動", "auto"),
            ]:
                with self.subTest(mode=mode, fan=fan), ThreadPoolExecutor() as pool:
                    row = {"名稱": AC["name"], "類型": "空調", "狀態": "啟用"}
                    ctx = SimpleNamespace(get=lambda name: [row])
                    pending = pool.submit(ha_climate.control, {"device_name": AC["name"],
                        "power": "on", "temperature": 29, "mode": mode, "fan_speed": fan}, ctx)
                    frame = ws.receive_json()
                    self.assertEqual(frame["patch"], {"power": "on", "temperature": 29,
                        "mode": expected_mode, "fan_speed": expected_fan})
                    ws.send_json({"type": "climate_result", "request_id": frame["request_id"],
                        "status": "success", "state": {**AC, "temperature": 29,
                        "hvac_mode": expected_mode, "fan_mode": expected_fan}})
                    self.assertEqual(pending.result(3).status, "success")
                    self.assertEqual(ctx._ac_saved_state["lastMode"], mode)
                    self.assertEqual(ctx._ac_saved_state["lastFanSpeed"], fan)

    def test_unknown_labels_are_rejected_without_sending_commands(self):
        ctx = SimpleNamespace(get=lambda name: [{"名稱": AC["name"], "類型": "空調", "狀態": "啟用"}])
        with patch.object(self.api.link, "command") as command:
            for field in ("mode", "fan_speed"):
                for value in ("不支援", "", [], True):
                    with self.subTest(field=field, value=value):
                        self.assertEqual(ha_climate.control({"device_name": AC["name"], field: value}, ctx).status, "failed")
            command.assert_not_called()

    def test_disconnect_returns_unknown_and_does_not_replay(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            pending = asyncio.run_coroutine_threadsafe(self.api.link.command(AC["name"], {"power": "off"}), self.api.link.loop)
            ws.receive_json()
        self.assertEqual(pending.result(3)["status"], "unknown")
        self.assertEqual(self.api.link.pending, {})
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            self.assertEqual(self.api.link.pending, {})
            self.assertTrue(ha_climate.status(AC["name"])["stateUncertain"])

    def test_wrong_device_result_cannot_confirm(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            pending = asyncio.run_coroutine_threadsafe(self.api.link.command(AC["name"], {"power": "off"}), self.api.link.loop)
            command = ws.receive_json()
            ws.send_json({"type": "climate_result", "request_id": command["request_id"],
                          "status": "success", "state": {**AC, "id": "d" * 32}})
            self.assertEqual(pending.result(3)["status"], "unknown")

    def test_malformed_configuration_does_not_restore_legacy(self):
        for bad in ("not-json", "{}", '["a", "a"]'):
            with patch.dict(os.environ, {"HOME_ASSISTANT_AC_NAMES": bad}):
                self.assertTrue(ha_climate.managed(AC["name"]))

    def test_migrated_feedback_and_manual_wrapper_skip_legacy(self):
        import ac_feedback
        legacy = Mock()
        ctx = SimpleNamespace()
        with patch.object(ha_climate, "control", return_value=SimpleNamespace(status="success")) as control:
            ac_feedback.manual_control(legacy)({"device_name": AC["name"]}, ctx)
            control.assert_called_once()
            legacy.assert_not_called()
            control.reset_mock()
            result = ac_feedback.manual_control(legacy)({"device_name": AC["name"]}, ctx, from_auto_schedule=True)
            self.assertEqual(result.status, "failed")
            control.assert_not_called()
        self.assertFalse(ac_feedback.config_for({"名稱": AC["name"], ac_feedback.CONFIG_COL: '{"enabled":true}'})["enabled"])
        with self.assertRaises(ValueError):
            ac_feedback.save_config(AC["name"], {})
