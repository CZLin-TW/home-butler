"""The theater relay runs beside AC control, never through the same slot."""
import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_home_assistant as base

HA = base.HA
AC = {"id": "c" * 32, "entity_id": "climate.living", "name": "客廳空調",
      "available": True, "hvac_mode": "cool", "temperature": 26, "fan_mode": "auto",
      "source_updated_at": 1000}
SUMMARY = {"flags": {"kef_link": True}, "devices": {"avr": "on"}}


class HaTheaterTests(unittest.TestCase):
    def setUp(self):
        base.HomeAssistantTests.setUp(self)
        self.env = patch.dict(os.environ, {"HOME_ASSISTANT_AC_NAMES": json.dumps([AC["name"]]),
                                           "THEATER_VIA_HA": "true"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.modules = patch.dict("sys.modules", {"home_assistant_api": self.api})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def connect(self, ws, theater=True):
        ws.send_json({"type": "hello", "protocol": 1, "token": HA,
                      "climate_control": True, "theater_relay": theater})
        capabilities = ws.receive_json()["capabilities"]
        self.assertIn("theater_relay", capabilities)
        ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "climates": [AC]})
        self.assertTrue(ws.receive_json()["accepted"])

    def relay(self, action="summary", payload=None):
        return asyncio.run_coroutine_threadsafe(
            self.api.link.theater_command(action, payload), self.api.link.loop)

    def test_theater_call_does_not_occupy_the_climate_slot(self):
        """The whole reason for a separate lane: a page load must not block AC."""
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            with ThreadPoolExecutor(max_workers=2) as pool:
                theater = pool.submit(lambda: self.relay().result(5))
                command = ws.receive_json()
                self.assertEqual(command["type"], "theater_command")
                self.assertEqual(command["action"], "summary")

                # AC control stays available while the theater call is in flight.
                climate = pool.submit(lambda: asyncio.run_coroutine_threadsafe(
                    self.api.link.command(AC["name"], {"power": "off"}), self.api.link.loop).result(5))
                dispatched = ws.receive_json()
                self.assertEqual(dispatched["type"], "climate_command")
                ws.send_json({"type": "climate_result", "request_id": dispatched["request_id"],
                              "status": "success", "state": {**AC, "hvac_mode": "off"}})
                self.assertEqual(climate.result(5)["status"], "success")

                ws.send_json({"type": "theater_result", "request_id": command["request_id"],
                              "status": "success", "result": SUMMARY})
                self.assertEqual(theater.result(5), {"status": "success", "result": SUMMARY,
                                                     "message": None})

    def test_second_theater_call_is_refused_without_queueing(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(lambda: self.relay().result(5))
                command = ws.receive_json()
                self.assertEqual(self.relay().result(5)["status"], "failed")
                ws.send_json({"type": "theater_result", "request_id": command["request_id"],
                              "status": "success", "result": SUMMARY})
                self.assertEqual(first.result(5)["status"], "success")

    def test_snapshot_reports_whether_the_relay_is_usable(self):
        """Checkable before THEATER_VIA_HA is switched on, not only after."""
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            self.assertTrue(self.api.link.snapshot()["theater_available"])
        # A dropped link makes the relay unusable even though HA declared it.
        self.assertFalse(self.api.link.snapshot()["theater_available"])
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws, theater=False)
            self.assertFalse(self.api.link.snapshot()["theater_available"])

    def test_relay_unavailable_without_the_capability(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws, theater=False)
            result = self.relay().result(5)
            self.assertEqual(result["status"], "failed")
            self.assertIn("未就緒", result["message"])

    def test_disconnect_leaves_the_result_unknown_and_is_not_replayed(self):
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.connect(ws)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(lambda: self.relay().result(5))
                ws.receive_json()
                ws.close()
                result = pending.result(5)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("success", json.dumps(result))

    def route(self):
        """Load the dispatch functions without the LINE stack config would pull."""
        from fastapi import HTTPException
        from test_callback_concurrency import endpoint
        env = {"HTTPException": HTTPException,
               "_agent_error": lambda code, err: HTTPException(status_code=code, detail=str(err)),
               "send_agent_command": self.agent_command}
        env["_theater_via_ha"] = endpoint("theater_api.py", "_theater_via_ha", env)
        return endpoint("theater_api.py", "_theater_command", env)

    async def agent_command(self, *args, **kwargs):
        self.agent_call = kwargs
        return {"status": "ok", "agent_id": "PC", "result": SUMMARY}

    def test_route_prefers_ha_and_reports_unknown_as_gateway_timeout(self):
        from fastapi import HTTPException

        async def unknown(action, payload=None):
            return {"status": "unknown", "message": "HA 劇院指令結果未確認；不會自動重送"}

        with patch.object(self.api.link, "theater_command", unknown):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(self.route()("theater.summary", {}))
        # Unknown may have landed at the theater agent; the caller must not retry.
        self.assertEqual(caught.exception.status_code, 504)

    def test_route_relays_a_successful_summary_through_ha(self):
        async def ok(action, payload=None):
            self.relayed = (action, payload)
            return {"status": "success", "result": SUMMARY, "message": None}

        with patch.object(self.api.link, "theater_command", ok):
            result = asyncio.run(self.route()("theater.summary", {}))
        self.assertEqual(self.relayed, ("summary", {}))
        self.assertEqual(result, {"agent_id": "home_assistant", **SUMMARY})

    def test_route_falls_back_to_the_pc_agent_when_the_flag_is_off(self):
        with patch.dict(os.environ, {"THEATER_VIA_HA": "false"}):
            result = asyncio.run(self.route()("theater.summary", {}))
        self.assertEqual(result["agent_id"], "PC")
        self.assertEqual(self.agent_call["required_capability"], "theater")


if __name__ == "__main__":
    unittest.main()
