"""Real FastAPI boundary, fake Sheets/hardware; no credentials or cloud calls."""
import ast
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OWNER, VOICE, BRIDGE = "owner-" + "a" * 32, "voice-" + "b" * 32, "bridge-" + "c" * 32
ROW = {"名稱": "客廳冷氣", "類型": "空調", "狀態": "啟用", "Device ID": "AC-ID",
       "位置": "客廳", "最後電源": "on", "最後溫度": 27, "最後模式": "冷氣",
       "最後風速": "低", "Auth": "private-auth"}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, rows):
        self.data = {"智能居家": copy.deepcopy(rows), "排程指令": []}
        self.load = Mock()
    def get(self, name):
        if name not in self.data:
            raise AssertionError("Personal worksheet read: " + name)
        return self.data[name]
    def set(self, name, value):
        self.data[name] = value


class HomebridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = ModuleType("config")
        self.config.HOME_BUTLER_API_KEY = OWNER
        self.config.DEVICE_VOICE_API_KEY = VOICE
        self.config.HOMEBRIDGE_API_KEY = BRIDGE
        self.config.HOMEBRIDGE_DEVICE_NAMES = '["客廳冷氣"]'
        self.ctx = Context([ROW])
        self.sheets = SimpleNamespace(RequestContext=Mock(return_value=self.ctx))
        self.status = load("bridge_test_status", "device_status.py")
        self.status.load_catalog([ROW])
        self.sensor = SimpleNamespace(snapshot=Mock(return_value={
            "客廳感測器": {"location": "客廳", "current": {"temp": 25}, "online": True,
                          "last_polled_at": 123, "history": ["private-history"]},
            "主臥感測器": {"location": "主臥", "current": {"temp": 24}, "online": True}}))
        self.control = Mock(side_effect=self.success)
        device = SimpleNamespace(control_ac_result=self.control)
        with patch.dict("sys.modules", {"config": self.config, "sheets": self.sheets,
                        "device_status": self.status, "sensor_state": self.sensor, "handlers.device": device}):
            self.api = load("bridge_test_api", "homebridge_api.py")
            self.auth = load("bridge_test_auth", "auth.py")
        app = FastAPI()
        app.include_router(self.api.router)
        @app.get("/owner", dependencies=[Depends(self.auth.verify_api_key)])
        def owner(): return {"ok": True}
        self.client = TestClient(app)
        self.id = self.api.device_key(ROW)

    def success(self, data, ctx):
        row = ctx.get("智能居家")[0]
        row["最後電源"] = data["power"]
        if "temperature" in data:
            row["最後溫度"] = data["temperature"]
        ctx._ac_state_saved = True
        return SimpleNamespace(status="success")

    def get(self, key=BRIDGE):
        return self.client.get("/api/homebridge/devices", headers={"X-API-Key": key})
    def post(self, data=None, key=BRIDGE, id=None, request_id=None):
        return self.client.post(f"/api/homebridge/devices/{id or self.id}/ac",
                                headers={"X-API-Key": key},
                                json={"request_id": request_id or str(uuid4()), **(data or {})})

    def test_auth_isolated_and_fail_closed(self):
        for key in [OWNER, VOICE, "", "wrong"]:
            self.assertEqual(self.get(key).status_code, 401)
            self.assertEqual(self.post({"power": "off"}, key=key).status_code, 401)
        self.assertEqual(self.client.get("/owner", headers={"X-API-Key": BRIDGE}).status_code, 401)
        for key in ["", "short", OWNER, VOICE]:
            self.config.HOMEBRIDGE_API_KEY = key
            self.assertEqual(self.get().status_code, 503)
        self.control.assert_not_called()

    def test_projection_no_sheet_io_and_state_updates(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["devices"][0]["temperature"], 27)
        self.assertEqual(len(response.json()["sensors"]), 1)
        for secret in ["private-auth", "AC-ID", "private-history", "主臥感測器"]:
            self.assertNotIn(secret, response.text)
        self.status.update("客廳冷氣", {"lastTemperature": 26, "lastPower": "off"})
        state = self.get().json()["devices"][0]
        self.assertEqual((state["power"], state["temperature"]), ("off", 26))
        self.sheets.RequestContext.assert_not_called()

    def test_cold_start_is_unavailable_not_empty(self):
        self.status.load_catalog([])
        self.assertEqual(self.get().status_code, 503)
        self.sheets.RequestContext.assert_not_called()

    def test_patch_retains_mode_and_fan_and_uses_original_handler(self):
        self.ctx.data["智能居家"][0]["最後模式"] = "除濕"
        response = self.post({"temperature": 26})
        self.assertEqual(response.json()["status"], "success")
        data = self.control.call_args.args[0]
        self.assertEqual(data, {"device_name": "客廳冷氣", "power": "on", "temperature": 26,
                                "mode": "dry", "fan_speed": "low"})
        self.ctx.load.assert_called_once_with(["智能居家", "排程指令"])

    def test_invalid_fields_types_ranges_and_off_combinations(self):
        for data in [{}, {"temperature": 26.5}, {"temperature": "26"}, {"temperature": True},
                     {"temperature": 31}, {"temperature": None}, {"power": "toggle"},
                     {"power": "off", "mode": "cool"}, {"antimold_final": True},
                     {"user_id": "owner"}, {"mode": "unexpected"}, {"fan_speed": "turbo"}]:
            self.assertEqual(self.post(data).status_code, 422, data)
        self.control.assert_not_called()

    def test_temperature_only_does_not_turn_on_off_or_unknown_ac(self):
        for power in ["off", ""]:
            self.ctx.data["智能居家"][0]["最後電源"] = power
            self.assertEqual(self.post({"temperature": 26}).status_code, 409)
        self.ctx.data["智能居家"][0]["最後風速"] = ""
        self.assertEqual(self.post({"power": "on"}).status_code, 409)
        self.control.assert_not_called()

    def test_exact_allowlist_rechecked_before_write_no_single_device_fallback(self):
        self.assertEqual(self.post({"power": "off"}, id="unknown").status_code, 404)
        self.ctx.data["智能居家"][0]["名稱"] = "主臥冷氣"
        self.assertEqual(self.post({"power": "off"}).status_code, 404)
        self.control.assert_not_called()

    def test_duplicate_names_ids_disabled_and_wrong_types_are_excluded(self):
        for rows in [[ROW, ROW], [ROW, {**ROW, "名稱": "別台"}], [{**ROW, "類型": "IR"}],
                     [{**ROW, "狀態": "停用"}], [{**ROW, "Device ID": ""}]]:
            self.status.load_catalog(rows)
            if rows[0].get("狀態") == "停用":
                self.assertEqual(self.get().status_code, 503)
            else:
                self.assertEqual(self.get().json()["devices"], [])

    def test_bad_allowlist_disables(self):
        for names in ["not json", '"客廳冷氣"', "[]", '[null]', '["客廳冷氣","客廳冷氣"]']:
            self.config.HOMEBRIDGE_DEVICE_NAMES = names
            self.assertEqual(self.get().status_code, 503)

    def test_antimold_result_reports_fan_on_instead_of_requested_off(self):
        def antimold(data, ctx):
            ctx.get("智能居家")[0].update({"最後電源": "on", "最後模式": "送風"})
            ctx._ac_state_saved = True
            return SimpleNamespace(status="success")
        self.control.side_effect = antimold
        result = self.post({"power": "off"}).json()
        self.assertEqual((result["device"]["power"], result["device"]["mode"]), ("on", "fan"))
        self.assertNotIn("antimold_final", self.control.call_args.args[0])

    def test_unknown_failure_and_persistence_failure_are_not_retried(self):
        for effect in [RuntimeError("secret-token"), lambda *_: SimpleNamespace(status="unknown"),
                       lambda *_: SimpleNamespace(status="success")]:
            self.control.reset_mock()
            self.control.side_effect = effect
            request_id = str(uuid4())
            first = self.post({"power": "off"}, request_id=request_id)
            again = self.post({"power": "off"}, request_id=request_id)
            self.assertEqual(first.json()["status"], "unknown")
            self.assertEqual(first.json(), again.json())
            self.assertNotIn("secret-token", first.text)
            self.control.assert_called_once()
            self.assertTrue(self.get().json()["devices"][0]["uncertain"])

    def test_request_id_dedup_and_conflict(self):
        request_id = str(uuid4())
        self.assertEqual(self.post({"power": "off"}, request_id=request_id).json()["status"], "success")
        self.post({"power": "off"}, request_id=request_id)
        self.assertEqual(self.post({"power": "on"}, request_id=request_id).status_code, 409)
        self.control.assert_called_once()

    def test_busy_rejects_without_command(self):
        with self.api._command_lock:
            self.assertEqual(self.post({"power": "off"}).status_code, 409)
        self.control.assert_not_called()

    def test_real_main_registers_separate_router(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(n, ast.Call) and ast.unparse(n) ==
                            "app.include_router(homebridge_router)" for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
