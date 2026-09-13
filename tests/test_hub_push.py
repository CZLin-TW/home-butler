"""Webhook-to-WebSocket boundaries without cloud requests or physical devices."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from ha_hub_events import hub_hint
import test_home_assistant as ha_tests
HA = ha_tests.HA

DEVICE = "AABBCCDDEEFF"


def report(**context):
    return {"eventType": "changeReport", "context": {"deviceType": "WoHub2", "deviceMac": DEVICE,
            "lightLevel": 1, "timeOfSample": time.time(), **context}}


class HubPushTests(unittest.TestCase):
    setUp = ha_tests.HomeAssistantTests.setUp

    def test_report_validation_seconds_milliseconds_and_replay(self):
        self.assertEqual(hub_hint(report()), DEVICE)
        self.assertEqual(hub_hint(report(deviceMac="aa:bb:cc:dd:ee:ff", timeOfSample=time.time()*1000)), DEVICE)
        for context in ({"deviceType": "WoHand"}, {"deviceMac": "unknown"}, {"lightLevel": True},
                        {"lightLevel": 21}, {"lightLevel": "1"}, {"timeOfSample": 1},
                        {"timeOfSample": float("nan")}, {"timeOfSample": time.time()+60}):
            self.assertIsNone(hub_hint(report(**context)))
        self.assertIsNone(hub_hint({"eventType": "other", "context": report()["context"]}))

    def test_authenticated_selection_routes_hint_not_values_and_replacement_clears_it(self):
        async def incoming(body: dict):
            return {"accepted": await self.api.link.hub_updates.notify(body)}
        self.client.app.add_api_route("/test-hint", incoming, methods=["POST"])
        with self.client as client:
            with client.websocket_connect("/api/home-assistant/ws") as ws:
                ws.send_json({"type": "hello", "protocol": 1, "token": HA, "hub_updates": True})
                self.assertIn("hub_updates", ws.receive_json()["capabilities"])
                ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "hub_devices": [DEVICE]})
                self.assertTrue(ws.receive_json()["accepted"])
                self.assertFalse(client.post("/test-hint", json=report(deviceMac="112233445566")).json()["accepted"])
                self.assertTrue(client.post("/test-hint", json=report()).json()["accepted"])
                frame = ws.receive_json()
                self.assertEqual(set(frame), {"type", "device_id", "received_at"})
                self.assertEqual(frame["device_id"], DEVICE)
                with client.websocket_connect("/api/home-assistant/ws") as replacement:
                    replacement.send_json({"type": "hello", "protocol": 1, "token": HA})
                    replacement.receive_json()
                    self.assertFalse(client.post("/test-hint", json=report()).json()["accepted"])
            self.assertFalse(client.post("/test-hint", json=report()).json()["accepted"])

    def test_subscriptions_validate_and_do_not_grant_control(self):
        for devices in ([DEVICE, DEVICE], ["light.room"], ["x"*12]):
            with self.assertRaises(ValueError):
                self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1,
                    "observations": [], "hub_devices": devices})


class HubBurstTests(unittest.IsolatedAsyncioTestCase):
    setUp = ha_tests.HomeAssistantTests.setUp

    async def test_burst_coalesces_trailing_edge_and_disconnect_cancels_pending(self):
        state, socket = self.api.LinkState(), AsyncMock()
        state.connect(socket, hub_capable=True)
        state.receive(socket, self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1,
            "observations": [], "hub_devices": [DEVICE]}))
        await state.hub_updates.notify(report())
        await asyncio.sleep(0.02)
        self.assertEqual(socket.send_json.await_count, 1)
        for _ in range(20):
            await state.hub_updates.notify(report())
        self.assertEqual(len(state.hub_updates.tasks), 1)
        # Let the one coalesced trailing notification send without a real 3s wait.
        state.hub_updates.last_sent[DEVICE] -= 3
        await asyncio.sleep(0.02)
        self.assertEqual(socket.send_json.await_count, 2)
        await state.hub_updates.notify(report())
        jobs = list(state.hub_updates.tasks.values())
        state.disconnect(socket)
        await asyncio.gather(*jobs, return_exceptions=True)
        self.assertFalse(state.hub_updates.pending)
        self.assertEqual(socket.send_json.await_count, 2)
