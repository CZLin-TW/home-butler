"""Real FastAPI/WebSocket tests, no app startup, Sheets or home hardware."""
import importlib.util
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
OWNER, HA, VOICE, BRIDGE = (letter * 40 for letter in "ohvb")
ROW = {"id": "a" * 32, "entity_id": "binary_sensor.fp2", "name": "客廳",
       "area": "客廳", "kind": "occupancy", "value": True, "available": True}


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HomeAssistantTests(unittest.TestCase):
    def setUp(self):
        self.config = ModuleType("config")
        for name, value in {"HOME_BUTLER_API_KEY": OWNER, "HOME_ASSISTANT_API_KEY": HA,
                            "DEVICE_VOICE_API_KEY": VOICE, "HOMEBRIDGE_API_KEY": BRIDGE}.items():
            setattr(self.config, name, value)
        with patch.dict("sys.modules", {"config": self.config}):
            auth = load("ha_test_auth", "auth.py")
            with patch.dict("sys.modules", {"auth": auth}):
                self.api = load("ha_test_api", "home_assistant_api.py")
        app = FastAPI()
        app.include_router(self.api.router)
        app.include_router(self.api.owner_router)
        self.client = TestClient(app)

    def read(self, key=OWNER):
        return self.client.get("/api/home-assistant/observations", headers={"X-API-Key": key})

    def connect(self, ws, key=HA):
        ws.send_json({"type": "hello", "protocol": 1, "token": key})
        self.assertEqual(ws.receive_json()["type"], "hello_ack")

    def send(self, ws, seq=1, rows=None):
        ws.send_json({"type": "snapshot", "sequence": seq, "observations": [ROW] if rows is None else rows})
        return ws.receive_json()

    def test_keys_are_separate_and_no_control_routes(self):
        for key in ("", OWNER, VOICE, BRIDGE):
            self.assertEqual(self.client.get("/api/home-assistant/health", headers={"X-API-Key": key}).status_code, 401)
        self.assertEqual(self.client.get("/api/home-assistant/health", headers={"X-API-Key": HA}).status_code, 200)
        self.assertEqual(self.read(HA).status_code, 401)
        self.assertEqual(self.read().status_code, 200)
        self.assertEqual(self.client.post("/api/home-assistant/control", json={}, headers={"X-API-Key": HA}).status_code, 404)
        for key in ("short", OWNER, VOICE, BRIDGE):
            self.config.HOME_ASSISTANT_API_KEY = key
            self.assertEqual(self.client.get("/api/home-assistant/health", headers={"X-API-Key": key}).status_code, 503)
            self.assertFalse(self.read().json()["configured"])

    def test_socket_authentication(self):
        for key in (OWNER, VOICE, BRIDGE, ""):
            with self.client.websocket_connect("/api/home-assistant/ws") as ws:
                ws.send_json({"type": "hello", "protocol": 1, "token": key})
                with self.assertRaises(WebSocketDisconnect) as error:
                    ws.receive_json()
                self.assertEqual(error.exception.code, 1008)

    def test_live_disconnect_reconnect_and_sequence(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            self.assertFalse(self.read().json()["online"])
            self.assertTrue(self.send(ws)["accepted"])
            self.assertIs(self.read().json()["observations"][0]["value"], True)
            self.assertFalse(self.send(ws, rows=[{**ROW, "value": False}])["accepted"])
            self.assertIs(self.read().json()["observations"][0]["value"], True)
            self.assertTrue(self.send(ws, 2, [{**ROW, "value": False}])["accepted"])
            self.assertIs(self.read().json()["observations"][0]["value"], False)
        self.assertFalse(self.read().json()["online"])
        self.assertIsNone(self.read().json()["observations"][0]["value"])
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            self.assertIsNone(self.read().json()["observations"][0]["value"])
            self.send(ws, rows=[])
            self.assertEqual(self.read().json()["observations"], [])

    def test_automation_report_is_optional_read_only_and_survives_a_dropped_link(self):
        def log(key=OWNER):
            return self.client.get("/api/home-assistant/automations", headers={"X-API-Key": key})
        self.assertEqual(log(HA).status_code, 401)
        self.assertEqual(log().json(), {"configured": True, "online": False, "shared": False,
                                        "received_at": None, "rules": [], "runs": []})
        rule = {"entity_id": "automation.demo_light", "name": "有人就開燈", "enabled": True, "last_triggered": 1760000000.5}
        run = {"entity_id": "automation.demo_light", "name": "有人就開燈", "at": 1760000000.5, "source": "state of binary_sensor.demo"}
        frame = {"type": "snapshot", "sequence": 1, "observations": [], "automations": {"rules": [rule], "runs": [run]}}
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            ws.send_json({"type": "hello", "protocol": 1, "token": HA})
            self.assertIn("automation_log", ws.receive_json()["capabilities"])
            ws.send_json(frame)
            self.assertTrue(ws.receive_json()["accepted"])
            live = log().json()
            self.assertEqual((live["online"], live["shared"], live["rules"], live["runs"]), (True, True, [rule], [run]))
            self.assertNotIn("automations", self.read().json())
            # A later snapshot without the report means the household stopped sharing it.
            ws.send_json({"type": "snapshot", "sequence": 2, "observations": []})
            self.assertTrue(ws.receive_json()["accepted"])
            self.assertEqual((log().json()["shared"], log().json()["runs"]), (False, []))
            ws.send_json({**frame, "sequence": 3})
            self.assertTrue(ws.receive_json()["accepted"])
        away = log().json()
        self.assertEqual((away["online"], away["shared"], away["runs"]), (False, True, [run]))
        self.assertIsNone(away["rules"][0]["enabled"])
        for path in ("/api/home-assistant/automations", "/api/home-assistant/automations/automation.demo_light"):
            for method in ("post", "put", "patch", "delete"):
                self.assertIn(getattr(self.client, method)(path, headers={"X-API-Key": OWNER}).status_code, (404, 405))

    def test_malformed_automation_reports_close_the_link(self):
        good_rule = {"entity_id": "automation.a", "name": "規則", "enabled": True}
        good_run = {"entity_id": "automation.a", "name": "規則", "at": 1.0}
        for report in ({"rules": [{**good_rule, "entity_id": "light.a"}], "runs": []},
                       {"rules": [{**good_rule, "enabled": "yes"}], "runs": []},
                       {"rules": [{**good_rule, "service": "automation.turn_off"}], "runs": []},
                       {"rules": [good_rule], "runs": [{**good_run, "at": -1}]},
                       {"rules": [good_rule], "runs": [{**good_run, "name": ""}]},
                       {"rules": [good_rule], "runs": [good_run] * 51},
                       {"rules": [good_rule]}, [good_rule]):
            with self.subTest(report=str(report)[:60]):
                with self.client.websocket_connect("/api/home-assistant/ws") as ws:
                    self.connect(ws)
                    ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "automations": report})
                    with self.assertRaises(WebSocketDisconnect) as error:
                        ws.receive_json()
                    self.assertEqual(error.exception.code, 1008)

    def test_stale_uses_receipt_clock_not_sensor_change_time(self):
        state, socket = self.api.LinkState(), object()
        state.connect(socket)
        with patch.object(self.api.time, "monotonic", return_value=10):
            state.receive(socket, self.api.Snapshot.model_validate(
                {"type": "snapshot", "sequence": 1, "observations": [{**ROW, "source_updated_at": 1}]}))
        with patch.object(self.api.time, "monotonic", return_value=99):
            self.assertTrue(state.snapshot()["online"])
        with patch.object(self.api.time, "monotonic", return_value=101):
            self.assertFalse(state.snapshot()["online"])
            self.assertIsNone(state.snapshot()["observations"][0]["value"])

    def test_old_socket_cannot_overwrite_replacement(self):
        state, old, new = self.api.LinkState(), object(), object()
        state.connect(old)
        state.connect(new)
        state.disconnect(old)
        frame = self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [ROW]})
        self.assertFalse(state.receive(old, frame))
        self.assertTrue(state.receive(new, frame))
        self.assertTrue(state.snapshot()["online"])

    def test_invalid_frames_fail_closed(self):
        invalid = [{**ROW, "value": "off"}, {**ROW, "available": False}, {**ROW, "token": "secret"},
                   {**ROW, "entity_id": "light.room"}, {**ROW, "source_updated_at": float("inf")},
                   {**ROW, "kind": "illuminance", "entity_id": "sensor.lux", "value": -1}]
        for row in invalid:
            with self.client.websocket_connect("/api/home-assistant/ws") as ws:
                self.connect(ws)
                ws.send_json({"type": "snapshot", "sequence": 1, "observations": [row]})
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()
        with self.assertRaises(ValueError):
            self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [ROW, ROW]})

    def test_unavailable_is_null_and_zero_lux_is_available(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            rows = [{**ROW, "available": False, "value": None},
                    {**ROW, "id": "b" * 32, "entity_id": "sensor.lux", "kind": "illuminance", "value": 0}]
            self.send(ws, rows=rows)
            values = self.read().json()["observations"]
            self.assertIsNone(values[0]["value"])
            self.assertEqual(values[1]["value"], 0)
            self.assertTrue(values[1]["available"])
