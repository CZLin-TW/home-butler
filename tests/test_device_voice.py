"""Capability tests: real FastAPI validation/auth, fake model/Sheets/hardware."""

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import device_voice as voice

ROOT = Path(__file__).resolve().parents[1]
OWNER_KEY = "owner-test-key-" + "a" * 32
DEVICE_KEY = "device-test-key-" + "b" * 32
ROWS = [
    {"名稱": "主臥電扇", "類型": "IR", "狀態": "啟用", "Device ID": "fan-secret-id", "按鈕": "風速、擺頭"},
    {"名稱": "主臥冷氣", "類型": "空調", "狀態": "啟用", "Device ID": "ac-secret-id", "Auth": "private-token"},
    {"名稱": "客廳除濕機", "類型": "除濕機", "狀態": "啟用", "Device ID": "dh-secret-id", "品牌": "LG"},
    {"名稱": "主臥溫濕度", "類型": "感應器", "狀態": "啟用", "Device ID": "sensor-secret-id"},
]


def action(name="control_ir", **args):
    return {"action": name, "args": [{"key": k, "value": v} for k, v in args.items()]}


def payload(*actions):
    return {"actions": list(actions)}


def fake_client(result):
    return SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(result, ensure_ascii=False))]))))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Mock()
        self.ctx.get.return_value = ROWS
        self.handlers = {key: Mock(return_value="✅ 指令已送出") for key in voice.ALLOWED_ARGS}

    def run_voice(self, result):
        return voice.run_device_voice("打開主臥電扇", self.ctx, fake_client(result), self.handlers)

    def test_allowed_commands_are_typed_and_fan_alias_is_resolved(self):
        prepared = voice.validate_actions(payload(
            action(device_name="主臥電風扇", button="開"),
            action("control_ac", device_name="主臥冷氣", temperature="26", fan_speed="low"),
            action("control_dehumidifier", device_name="客廳除濕機", humidity="55", mode="智慧除濕"),
            action("query_sensor", device_name="主臥溫濕度")), ROWS)
        self.assertEqual(prepared[0][1]["device_name"], "主臥電扇")
        self.assertEqual(prepared[1][1]["temperature"], 26)
        self.assertEqual(prepared[2][1]["humidity"], 55)

    def test_only_device_catalog_reaches_model_no_private_pages_or_history(self):
        client = fake_client(payload(action("query_devices")))
        voice.run_device_voice("列出設備", self.ctx, client, self.handlers)
        self.ctx.load.assert_called_once_with(["智能居家"])
        self.ctx.get.assert_called_once_with("智能居家")
        call = client.messages.create.call_args.kwargs
        self.assertEqual(len(call["messages"]), 1)
        model_data = json.loads(call["messages"][0]["content"])
        for row in model_data["devices"]:
            self.assertEqual(set(row), set(voice.CATALOG_FIELDS))
        self.assertNotIn("private-token", json.dumps(call))
        self.assertNotIn("secret-id", json.dumps(call))
        self.assertEqual(len(self.ctx.method_calls), 2)

    def test_inventory_excludes_disabled_unsupported_and_future_private_columns(self):
        rows = [*ROWS, {**ROWS[0], "狀態": "停用", "名稱": "停用電扇"},
                {**ROWS[0], "類型": "未支援", "名稱": "未支援設備"}]
        rows[0] = {**rows[0], "未來憑證欄": "sensitive"}
        self.ctx.get.return_value = rows
        projected = voice.device_catalog(rows)
        self.assertEqual(len(projected), len(ROWS))
        self.assertNotIn("sensitive", json.dumps(projected))
        reply = voice.device_list_reply({}, self.ctx)
        self.assertIn("主臥電扇", reply)
        self.assertNotIn("停用", reply)
        self.assertNotIn("未支援", reply)

    def test_non_json_model_output_never_becomes_a_reply_or_command(self):
        client = fake_client({})
        client.messages.create.return_value.content[0].text = "已刪除待辦"
        self.assertEqual(voice.run_device_voice("查待辦", self.ctx, client, self.handlers), voice.CLARIFY)
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_mixed_forbidden_batch_never_partially_executes(self):
        for forbidden in ["query_todo", "delete_todo", "add_food", "query_food", "query_members",
                          "add_schedule", "set_dehumidifier_auto", "unsupported"]:
            with self.subTest(forbidden=forbidden):
                reply = self.run_voice(payload(action(device_name="主臥電扇", button="開"), action(forbidden)))
                self.assertEqual(reply, voice.REFUSAL)
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_invalid_arguments_and_targets_block_whole_batch(self):
        bad = [
            action(device_name="客房電扇", button="開"),
            action(device_name="主臥電扇", button="未設定鍵"),
            action(device_name="主臥電扇"), action(button="開"),
            action("control_ac", device_name="主臥電扇", power="on"),
            action("control_ac", device_name="主臥冷氣", antimold_final="true", power="off"),
            action("control_ac", device_name="主臥冷氣", temperature="31"),
            action("control_ac", device_name="主臥冷氣", temperature="26.5"),
            action("control_ac", device_name="主臥冷氣", power="toggle"),
            action("control_ac", device_name="主臥冷氣", power="off", temperature="26"),
            action("control_ac", device_name="客房冷氣", power="on"),
            action("control_dehumidifier", device_name="客房除濕機", power="on"),
            action("control_dehumidifier", device_name="客廳除濕機", humidity="57"),
            action("control_dehumidifier", device_name="客廳除濕機", mode="防霉抑菌"),
            action("control_dehumidifier", device_name="客廳除濕機", _internal="true", power="on"),
            action("query_devices", user_id="owner"),
            action(device_name="主臥電扇", button={"action": "delete_todo"}),
        ]
        for entry in bad:
            with self.subTest(entry=entry):
                self.run_voice(payload(action(device_name="主臥電扇", button="開"), entry))
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_duplicate_args_malformed_output_and_too_many_actions_are_rejected(self):
        duplicate = action(device_name="主臥電扇", button="開")
        duplicate["args"].append({"key": "button", "value": "關"})
        for result in [None, [], {"reply": "已刪除待辦"}, payload(), payload(duplicate),
                       payload(*[action("query_devices")] * 5), payload({"action": [], "args": []}),
                       payload({"action": "query_devices", "args": {}})]:
            with self.subTest(result=result):
                self.run_voice(result)
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_disabled_and_duplicate_names_never_reach_legacy_fallback(self):
        for rows in [[{**ROWS[1], "狀態": "停用"}], [ROWS[1], {**ROWS[0], "名稱": "主臥冷氣"}]]:
            with self.assertRaises(voice.VoicePolicyError):
                voice.validate_actions(payload(action("control_ac", device_name="主臥冷氣", power="on")), rows)

    def test_reply_is_actual_handler_result_and_no_emoji(self):
        self.handlers["control_ir"].return_value = "✅ 主臥電扇「開」指令已送出"
        self.assertEqual(self.run_voice(payload(action(device_name="主臥電扇", button="開"))),
                         "已送出主臥電扇的電源指令。")
        self.handlers["control_ir"].assert_called_once_with({"device_name": "主臥電扇", "button": "開"}, self.ctx)

    def test_execution_exception_keeps_prior_results_and_does_not_retry(self):
        self.handlers["control_ac"].side_effect = RuntimeError("provider-secret")
        result = self.run_voice(payload(action(device_name="主臥電扇", button="開"),
                                        action("control_ac", device_name="主臥冷氣", power="on"),
                                        action("query_devices")))
        self.assertIn("指令已送出", result)
        self.assertIn("結果未確認", result)
        self.assertNotIn("provider-secret", result)
        self.handlers["control_ac"].assert_called_once()
        self.handlers["query_devices"].assert_not_called()


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouteTests(unittest.TestCase):
    def setUp(self):
        config = ModuleType("config")
        config.HOME_BUTLER_API_KEY, config.DEVICE_VOICE_API_KEY = OWNER_KEY, DEVICE_KEY
        config.claude = fake_client(payload(action("query_devices")))
        device = ModuleType("handlers.device")
        for name in voice.ALLOWED_ARGS:
            setattr(device, "handle_" + name, Mock(return_value="已設定的設備"))
        sheets = ModuleType("sheets")
        self.ctx = Mock()
        self.ctx.get.return_value = ROWS
        sheets.RequestContext = Mock(return_value=self.ctx)
        with patch.dict("sys.modules", {"config": config, "handlers.device": device, "sheets": sheets}):
            self.auth = load_module("voice_test_auth", "auth.py")
            with patch.dict("sys.modules", {"auth": self.auth}):
                self.api = load_module("voice_test_api", "device_voice_api.py")
        app = FastAPI()
        app.include_router(self.api.router)
        # Real owner verifier shared by existing routers (wiring checked below).
        @app.post("/api/assistant", dependencies=[Depends(self.auth.verify_api_key)])
        def owner_api():
            return {"reply": "owner"}
        self.client = TestClient(app)

    def post(self, key=DEVICE_KEY, body=None, path="/api/assistant/devices"):
        return self.client.post(path, headers={"X-API-Key": key}, json=body if body is not None else {"text": "有哪些設備"})

    def test_restricted_key_only_works_at_its_dedicated_endpoint(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(path="/api/assistant").status_code, 401)
        self.assertEqual(self.post(key=OWNER_KEY).status_code, 401)
        self.assertEqual(self.post(key=OWNER_KEY, path="/api/assistant").status_code, 200)
        for key in ["", "wrong"]:
            self.assertEqual(self.post(key=key).status_code, 401)
        self.assertEqual(self.client.get("/api/assistant/devices", headers={"X-API-Key": DEVICE_KEY}).status_code, 405)

    def test_unconfigured_short_and_reused_key_disable_feature_only(self):
        for key in ["", "short", OWNER_KEY]:
            with patch.object(self.auth, "DEVICE_VOICE_API_KEY", key):
                self.assertEqual(self.post(key=key).status_code, 503)
                self.assertEqual(self.post(key=OWNER_KEY, path="/api/assistant").status_code, 200)
        self.ctx.load.assert_not_called()

    def test_key_rotation_invalidates_old_device_key_without_changing_owner_key(self):
        with patch.object(self.auth, "DEVICE_VOICE_API_KEY", "rotated-" + "c" * 32):
            self.assertEqual(self.post().status_code, 401)
            self.assertEqual(self.post(key="rotated-" + "c" * 32).status_code, 200)
            self.assertEqual(self.post(key=OWNER_KEY, path="/api/assistant").status_code, 200)

    def test_identity_overrides_and_invalid_bodies_are_rejected_before_io(self):
        for extra in [{"user_id": "owner"}, {"user_id": ""}, {"role": "admin"}, {"mode": "full"}, {"actions": []}]:
            self.assertEqual(self.post(body={"text": "查待辦", **extra}).status_code, 422)
        for body in [{}, {"text": ""}, {"text": "x" * 501}, {"text": 123}, {"text": None}]:
            self.assertEqual(self.post(body=body).status_code, 422)
        self.assertEqual(self.post(body={"text": "   "}).status_code, 400)
        self.ctx.load.assert_not_called()

    def test_provider_failure_does_not_leak_exception_or_send_command(self):
        self.api.claude.messages.create.side_effect = RuntimeError("secret-token")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-token", response.text)
        for handler in list(self.api.DEVICE_HANDLERS.values())[:-1]:
            handler.assert_not_called()

    def test_production_router_registration_and_owner_dependency_remain_separate(self):
        main = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(node, ast.Call) and ast.unparse(node) ==
                            "app.include_router(device_voice_router)" for node in ast.walk(main)))
        web = ast.parse((ROOT / "web_api.py").read_text(encoding="utf-8"))
        assignment = next(node for node in web.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets))
        self.assertIn("Depends(verify_api_key)", ast.unparse(assignment))
        self.assertEqual(set(self.api.DEVICE_HANDLERS), set(voice.ALLOWED_ARGS))
        self.assertEqual([route.path for route in self.api.router.routes], ["/api/assistant/devices"])


if __name__ == "__main__":
    unittest.main()
