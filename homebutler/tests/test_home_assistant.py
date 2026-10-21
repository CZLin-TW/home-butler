"""The Home Assistant connection: who may connect, snapshots, commands, and the SwitchBot hint relay."""
import asyncio
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from homebutler.app import create_app
from homebutler.domain import tokens
from homebutler.integrations import switchbot_push
from homebutler.integrations.ha.link import STALE_SECONDS, Link
from homebutler.integrations.ha.messages import ClimateResult, Snapshot, TheaterResult
from homebutler.settings import Settings
from homebutler.tests.helpers import StoreTest

AC = "a" * 32


def climate(**change):
    return {"id": AC, "entity_id": "climate.living", "name": "冷氣", "area": "客", "floor_id": "a", "available": True,
            "hvac_mode": "cool", "temperature": 26, "fan_mode": "auto", "source_updated_at": 100.0, **change}


def snapshot(sequence, **change):
    return Snapshot.model_validate({"type": "snapshot", "sequence": sequence, "observations": [], "climates": [climate(**change)]})


class Socket:
    def __init__(self):
        self.sent = []
    async def send_json(self, frame):
        self.sent.append(frame)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.link = Link(clock=lambda: self.now[0], wall=lambda: self.now[0])
        self.socket = Socket()
        self.link.connect(self.socket, {"climate_control", "theater_relay", "observations"})

    def test_offline_until_a_snapshot_and_again_when_it_is_stale(self):
        self.assertFalse(self.link.online())
        self.assertTrue(self.link.receive(self.socket, snapshot(1)))
        self.assertTrue(self.link.state()["climates"][0]["available"])
        self.now[0] += STALE_SECONDS + 1
        self.assertFalse(self.link.online())
        self.assertFalse(self.link.state()["climates"][0]["available"])

    def test_old_sockets_and_old_sequences_are_ignored(self):
        self.link.receive(self.socket, snapshot(2))
        self.assertFalse(self.link.receive(self.socket, snapshot(1)))
        newer = Socket()
        self.assertIs(self.link.connect(newer, {"climate_control"}), self.socket)
        self.assertFalse(self.link.receive(self.socket, snapshot(9)))
        self.assertFalse(self.link.online())                     # a reconnect starts unknown

    def run_command(self, respond):
        async def go():
            task = asyncio.create_task(self.link.climate_command(AC, {"hvac_mode": "off"}))
            await asyncio.sleep(0)
            frame = self.socket.sent[-1]
            respond(frame)
            return await task
        return asyncio.run(go())

    def test_a_confirmed_command_updates_the_state_and_clears_uncertainty(self):
        self.link.receive(self.socket, snapshot(1))
        result = self.run_command(lambda f: self.link.climate_result(self.socket, ClimateResult.model_validate(
            {"type": "climate_result", "request_id": f["request_id"], "status": "success", "state": climate(hvac_mode="off", source_updated_at=200.0)})))
        self.assertEqual(result["status"], "success")
        state = self.link.climate(AC)
        self.assertEqual((state["hvac_mode"], state["uncertain"]), ("off", False))
        self.link.receive(self.socket, snapshot(2))              # a poll sampled before the command
        self.assertEqual(self.link.climate(AC)["hvac_mode"], "off")

    def test_unknown_results_and_disconnects_are_never_success_and_mark_uncertain(self):
        self.link.receive(self.socket, snapshot(1))
        result = self.run_command(lambda f: self.link.disconnect(self.socket))
        self.assertEqual(result["status"], "unknown")
        self.link.connect(self.socket, {"climate_control"}); self.link.receive(self.socket, snapshot(1))
        self.assertTrue(self.link.climate(AC)["uncertain"])
        self.link.receive(self.socket, snapshot(2, source_updated_at=300.0))   # HA reported a newer state
        self.assertFalse(self.link.climate(AC)["uncertain"])

    def test_a_result_from_another_socket_or_request_does_nothing(self):
        self.link.receive(self.socket, snapshot(1))
        other = Socket()
        def respond(frame):
            self.link.climate_result(other, ClimateResult.model_validate(
                {"type": "climate_result", "request_id": frame["request_id"], "status": "success", "state": climate()}))
            self.link.climate_result(self.socket, ClimateResult.model_validate(
                {"type": "climate_result", "request_id": "b" * 32, "status": "success", "state": climate()}))
            self.link.climate_result(self.socket, ClimateResult.model_validate(
                {"type": "climate_result", "request_id": frame["request_id"], "status": "failed"}))
        self.assertEqual(self.run_command(respond)["status"], "failed")

    def test_one_device_command_at_a_time_and_the_theater_has_its_own_lane(self):
        self.link.receive(self.socket, snapshot(1))
        async def go():
            first = asyncio.create_task(self.link.climate_command(AC, {"hvac_mode": "off"}))
            await asyncio.sleep(0)
            busy = await self.link.climate_command(AC, {"hvac_mode": "cool"})
            theater = asyncio.create_task(self.link.theater_command("summary"))
            await asyncio.sleep(0)
            frame = [f for f in self.socket.sent if f["type"] == "theater_command"][-1]
            self.link.theater_result(self.socket, TheaterResult.model_validate(
                {"type": "theater_result", "request_id": frame["request_id"], "status": "success", "result": {"on": True}}))
            self.link.disconnect(self.socket)
            return busy, await theater, await first
        busy, theater, first = asyncio.run(go())
        self.assertEqual(busy["status"], "failed"); self.assertIn("請稍後", busy["message"])
        self.assertEqual(theater["status"], "success")
        self.assertEqual(first["status"], "unknown")

    def test_commands_are_refused_when_not_ready(self):
        self.assertEqual(asyncio.run(self.link.climate_command(AC, {}))["status"], "failed")       # nothing received yet
        self.link.receive(self.socket, snapshot(1, available=False))
        self.assertEqual(asyncio.run(self.link.climate_command(AC, {}))["status"], "failed")
        self.assertEqual(asyncio.run(self.link.hue_command("hue.delete_everything", {}))["status"], "failed")


class WebSocketTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(database_schema=self.schema), store=self.store, start_jobs=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.store.transaction() as s:
            self.ha_token, self.key = tokens.issue(s, "home_assistant", "HA")
            _, self.dashboard = tokens.issue(s, "dashboard", "Dashboard")

    def hello(self, ws, key, protocol=2):
        ws.send_json({"type": "hello", "protocol": protocol, "token": key, "climate_control": True})

    def test_health_says_the_protocol_to_the_ha_key_only(self):
        self.assertEqual(self.client.get("/v2/home-assistant/health", headers={"Authorization": "Bearer " + self.key}).json(), {"protocol": 2})
        self.assertEqual(self.client.get("/v2/home-assistant/health", headers={"Authorization": "Bearer " + self.dashboard}).status_code, 401)
        self.assertEqual(self.client.get("/v2/home-assistant/health").status_code, 401)

    def test_the_old_integration_finds_its_addresses(self):
        self.assertEqual(self.client.get("/api/home-assistant/health", headers={"X-API-Key": self.key}).json(), {"protocol": 1})
        self.assertEqual(self.client.get("/api/home-assistant/health", headers={"X-API-Key": self.dashboard}).status_code, 401)
        with self.client.websocket_connect("/api/home-assistant/ws") as ws:
            self.hello(ws, self.key, protocol=1)
            ack = ws.receive_json()
            self.assertEqual((ack["type"], ack["protocol"]), ("hello_ack", 1))

    def test_a_valid_key_connects_and_snapshots_are_acknowledged(self):
        with self.client.websocket_connect("/v2/home-assistant/ws") as ws:
            self.hello(ws, self.key, protocol=1)
            self.assertEqual(ws.receive_json()["type"], "hello_ack")
            ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "climates": [climate()]})
            self.assertEqual(ws.receive_json(), {"type": "snapshot_ack", "sequence": 1, "accepted": True})
            self.assertTrue(self.app.state.ha.online())
            self.assertIn("climate_control", self.app.state.ha.capabilities)

    def test_wrong_keys_bad_hellos_and_malformed_snapshots_are_refused(self):
        for hello in ({"type": "hello", "protocol": 2, "token": self.dashboard}, {"type": "hello", "protocol": 2, "token": "x"},
                      {"type": "hello", "protocol": 9, "token": self.key}, {"type": "nope"}):
            with self.client.websocket_connect("/v2/home-assistant/ws") as ws:
                ws.send_json(hello)
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()
        with self.client.websocket_connect("/v2/home-assistant/ws") as ws:
            self.hello(ws, self.key); ws.receive_json()
            ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "climates": [climate(temperature=99)]})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_revoking_the_key_cuts_the_connection_on_the_next_frame(self):
        with self.client.websocket_connect("/v2/home-assistant/ws") as ws:
            self.hello(ws, self.key); ws.receive_json()
            with self.store.transaction() as s:
                tokens.revoke(s, self.ha_token.id)
            ws.send_json({"type": "snapshot", "sequence": 1, "observations": []})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertFalse(self.app.state.ha.online())


class HubRelayTests(unittest.TestCase):
    def body(self, **change):
        context = {"deviceType": "WoHub2", "deviceMac": "AA:BB:CC:DD:EE:FF", "lightLevel": 7, "timeOfSample": 1000}
        return {"eventType": "changeReport", "context": {**context, **change}}

    def test_only_recent_hub_2_changes_count(self):
        now = 1_760_000_000
        self.assertEqual(switchbot_push.hub_changed(self.body(timeOfSample=now - 10), now=now), "AABBCCDDEEFF")
        self.assertEqual(switchbot_push.hub_changed(self.body(timeOfSample=(now - 10) * 1000), now=now), "AABBCCDDEEFF")   # ms
        for bad in (self.body(timeOfSample=now - 500), self.body(deviceType="Meter"), self.body(lightLevel=21),
                    self.body(deviceMac="zz"), {"eventType": "other"}, None):
            self.assertIsNone(switchbot_push.hub_changed(bad, now=now))

    def test_relays_only_hubs_ha_asked_for_never_the_value(self):
        link = Link()
        socket = Socket()
        link.connect(socket, {"hub_updates"})
        link.receive(socket, Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [], "hub_devices": ["AABBCCDDEEFF"]}))
        relay = switchbot_push.HubRelay(link)
        import time
        now = time.time()
        async def go():
            ok = await relay.notify(self.body(timeOfSample=now))
            other = await relay.notify(self.body(timeOfSample=now, deviceMac="11:22:33:44:55:66"))
            await asyncio.sleep(0.05)
            return ok, other
        ok, other = asyncio.run(go())
        self.assertEqual((ok, other), (True, False))
        self.assertEqual(len(socket.sent), 1)
        self.assertEqual(set(socket.sent[0]), {"type", "device_id", "received_at"})     # no light level crosses


if __name__ == "__main__":
    unittest.main()
