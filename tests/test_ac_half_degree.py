"""Half-degree contracts through real handlers/models, with fake external I/O."""
import ast
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from ac_temperature import comfort_temperature, ir_temperature
from command_result import CommandResult
from test_callback_concurrency import endpoint


class HalfDegreeTests(unittest.TestCase):
    def test_model_string_arguments_keep_half_degree_for_direct_and_scheduled_control(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "prompt.py").read_text(encoding="utf-8"))
        env = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
               and n.targets[0].id in {"ARG_KEY_TYPES", "SCHEDULE_CMD_KEYS"}}
        env["_coerce_arg"] = endpoint("assistant.py", "_coerce_arg", env)
        flatten = endpoint("assistant.py", "_flatten_action", env)
        for action, nested in [("control_ac", None), ("add_schedule", "params"), ("modify_schedule", "params_new")]:
            result = flatten({"action": action, "args": [{"key": "temperature", "value": "26.5"}]})
            self.assertEqual((result[nested] if nested else result)["temperature"], 26.5)

    def handler(self, enabled):
        row = {"名稱": "空調", "Device ID": "ac", "類型": "空調", "狀態": "啟用",
               "最後電源": "on", "最後模式": "冷氣", "最後溫度": 26.5,
               "最後開機時間": "2026-09-08 10:00", "最後風速": "低",
               "空調溫度回饋設定": json.dumps({"enabled": enabled, "sensor_name": "室溫"})}
        ctx = SimpleNamespace(get=lambda _: [row], load=Mock())
        api = SimpleNamespace(ac_set_all=Mock(return_value={"success": True}),
            ac_turn_off=Mock(return_value={"success": True}), AC_MODE_MAP={"cool": 2, "heat": 5}, AC_FAN_MAP={"low": 2})
        def save(ctx, device_id, power, temperature=None, mode=None, fan=None, **kw):
            ctx._ac_state_saved = True
            ctx._ac_saved_state = {"lastPower": power, "lastTemperature": temperature}
        saver = Mock(side_effect=save)
        env = {"CommandResult": CommandResult, "get_device_id_by_name": lambda *args: "ac", "switchbot_api": api,
               "now_taipei": lambda: datetime(2026, 9, 8, 12), "_save_ac_last_state": saver,
               "maintain_ac_auto_schedule": Mock(), "_cancel_antimold_schedules": Mock(),
               "_should_antimold": lambda *args: True, "_antimold_fan_minutes": lambda *args: 5,
               "_schedule_antimold_off": Mock()}
        return endpoint("handlers/device.py", "control_ac_result", env), ctx, api, saver

    def test_whole_ir_and_half_comfort_are_separate_for_cooling_and_heating(self):
        for enabled in [False, True]:
            for mode in ["cool", "heat"]:
                fn, ctx, api, save = self.handler(enabled)
                result = fn({"device_name": "空調", "power": "on", "temperature": 26.5, "mode": mode}, ctx)
                self.assertEqual(result.status, "success")
                self.assertEqual(api.ac_set_all.call_args.args[1], 27)
                self.assertEqual(save.call_args.args[3], 26.5 if enabled else 27)
                self.assertEqual(ctx._ac_saved_state["lastTemperature"], 26.5 if enabled else 27)

    def test_invalid_target_never_sends_and_half_up_is_explicit(self):
        for v in [None, True, float("nan"), float("inf"), 15.5, 30.5, 26.2]:
            with self.assertRaises(ValueError): comfort_temperature(v)
            fn, ctx, api, save = self.handler(True)
            self.assertEqual(fn({"temperature": v}, ctx).status, "failed")
            api.ac_set_all.assert_not_called(); save.assert_not_called()
        for v, expected in [(16, 16), (16.5, 17), (26.5, 27), (27.5, 28), (30, 30)]:
            self.assertEqual(ir_temperature(v), expected)

    def test_antimold_keeps_half_comfort_but_sends_integer_ir(self):
        fn, ctx, api, save = self.handler(True)
        self.assertEqual(fn({"power": "off"}, ctx).status, "success")
        api.ac_set_all.assert_called_once_with("ac", 27, 4, 1, "on")
        self.assertEqual(save.call_args.args[3], 26.5)

    def test_dashboard_http_accepts_half_degree_and_returns_backend_target(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "web_api.py").read_text(encoding="utf-8"))
        node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "AcControlRequest")
        env = {"BaseModel": BaseModel, "Optional": Optional, "Field": Field, "HTTPException": HTTPException}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "web_api.py", "exec"), env)
        for enabled in [False, True]:
            core, ctx, api, _ = self.handler(enabled)
            env.update(RequestContext=lambda: ctx, control_ac_result=core)
            fn = endpoint("web_api.py", "api_control_ac", env)
            fn.__annotations__["req"] = env["AcControlRequest"]
            app = FastAPI(); app.post("/ac")(fn); client = TestClient(app)
            response = client.post("/ac", json={"device_name": "空調", "temperature": 26.5})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"]["lastTemperature"], 26.5 if enabled else 27)
            api.ac_set_all.reset_mock()
            for value in [26.2, "26.5", True, 30.5]:
                self.assertEqual(client.post("/ac", json={"temperature": value}).status_code, 422)
            api.ac_set_all.assert_not_called()
