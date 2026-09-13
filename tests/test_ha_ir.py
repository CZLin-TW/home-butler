"""Actual handler and WebSocket boundary; never contact SwitchBot."""
import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_home_assistant as base
import ha_ir
from command_result import CommandResult
from device_name_resolution import resolve_ir_device
from test_callback_concurrency import endpoint

B = {"id": "d" * 32, "entity_id": "button.fan_up", "name": "主臥電扇", "button": "風速+", "available": True}


class HAIRTests(unittest.TestCase):
    def setUp(self):
        base.HomeAssistantTests.setUp(self)
        for ctx in (patch.dict(os.environ, {"HOME_ASSISTANT_IR_NAMES": json.dumps([B["name"]])}),
                    patch.dict("sys.modules", {"home_assistant_api": self.api})):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.direct = Mock()
        self.handler = endpoint("handlers/device.py", "control_ir_result", {
            "CommandResult": CommandResult, "resolve_ir_device": resolve_ir_device,
            "switchbot_api": SimpleNamespace(ir_control=self.direct)})

    def connect(self, ws):
        ws.send_json({"type": "hello", "protocol": 1, "token": base.HA, "ir_control": True})
        self.assertIn("ir_control", ws.receive_json()["capabilities"])
        ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "ir_buttons": [B]})
        self.assertTrue(ws.receive_json()["accepted"])

    def call(self):
        return self.handler({"device_name": "主臥電風扇", "button": "風速+"}, SimpleNamespace(get=lambda _: [
            {"名稱": B["name"], "類型": "IR", "狀態": "啟用", "Device ID": "selected-device"}]))

    def test_actual_dashboard_button_dispatch_and_provider_failure(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            for status in ("success", "failed", "unknown"):
                with ThreadPoolExecutor() as pool:
                    pending = pool.submit(self.call)
                    frame = ws.receive_json()
                    self.assertEqual((frame["type"], frame["id"], frame["name"], frame["button"]),
                                     ("ir_command", B["id"], B["name"], "風速+"))
                    ws.send_json({"type": "ir_result", "request_id": frame["request_id"], "status": status})
                    self.assertEqual(pending.result(3).status, status)
        self.direct.assert_not_called()

    def test_offline_and_malformed_migration_never_fall_back(self):
        self.assertEqual(self.call().status, "failed")
        with patch.dict(os.environ, {"HOME_ASSISTANT_IR_NAMES": "bad json"}):
            self.assertTrue(ha_ir.managed(B["name"]))
            self.assertEqual(self.call().status, "failed")
        self.direct.assert_not_called()

    def test_only_selected_available_button_and_no_concurrent_queue(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            for name, button in (("別台", "風速+"), (B["name"], "刪除")):
                f = asyncio.run_coroutine_threadsafe(self.api.link.ir_command(name, button), self.api.link.loop)
                self.assertEqual(f.result(3)["status"], "failed")
            first = asyncio.run_coroutine_threadsafe(self.api.link.ir_command(B["name"], "風速+"), self.api.link.loop)
            frame = ws.receive_json()
            second = asyncio.run_coroutine_threadsafe(self.api.link.ir_command(B["name"], "風速+"), self.api.link.loop)
            self.assertEqual(second.result(3)["status"], "failed")
        self.assertEqual(first.result(3)["status"], "unknown")
        self.assertEqual(self.api.link.pending, {})
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)  # First response is a snapshot ACK, not replayed command.

    def test_duplicate_or_ambiguous_snapshots_rejected(self):
        from pydantic import ValidationError
        for other in (B, {**B, "id": "e" * 32, "entity_id": "button.other"}):
            with self.assertRaises(ValidationError):
                self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [], "ir_buttons": [B, other]})
