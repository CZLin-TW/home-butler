"""Actual backend projection/routing, fake external transport and Sheets."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_home_assistant as link_tests
import test_sensor_polling as polling_tests
from test_home_assistant import HA
from test_sensor_polling import SENSOR
from test_callback_concurrency import endpoint


class UnifiedSensorTests(unittest.TestCase):
    setUp = polling_tests.SensorPollingTests.setUp

    def test_ha_is_live_source_compensated_once_history_only_on_sampling(self):
        import ha_sensors
        snapshot = {"received_at": 12000, "age_seconds": 0, "environment": [
            {"name": "室溫", "kind": "temperature", "value": 28, "available": True},
            {"name": "室溫", "kind": "humidity", "value": 60, "available": True},
            {"name": "室溫", "kind": "co2", "value": 850, "available": True},
        ]}
        with patch.dict("os.environ", {"HOME_ASSISTANT_SENSOR_NAMES": '["室溫"]'}), patch.dict("sys.modules", {
                "home_assistant_api": SimpleNamespace(link=SimpleNamespace(snapshot=lambda: snapshot))}):
            for _ in range(5):
                self.assertEqual(self.state.snapshot()["室溫"]["current"]["temp"], 27)
                self.assertEqual(self.status.snapshot()["室溫"]["humidity"], 58)
            self.append.assert_not_called()
            self.assertEqual(self.poll.refresh(SENSOR)["co2"], 850)
            self.state.record_history("室溫")
            self.state.record_history("室溫")
            self.append.assert_called_once()
            snapshot["environment"][0]["value"] = 29
            self.assertEqual(self.state.snapshot()["室溫"]["current"]["temp"], 28)
            self.assertEqual(self.state.snapshot()["室溫"]["history"][0]["temp"], 27)
            for item in snapshot["environment"]:
                item.update(available=False, value=None)
            self.assertIn("error", self.poll.refresh(SENSOR))
            current = self.state.snapshot()["室溫"]
            self.assertFalse(current["online"])
            self.assertIsNone(current["current"]["temp"])
            self.assertEqual(len(current["history"]), 1)
            self.assertIsNone(self.status.snapshot()["室溫"]["temperature"])
            self.sdk.get_hub_sensor.assert_not_called()
            self.assertTrue(ha_sensors.managed("室溫"))

    def test_invalid_migration_config_never_falls_back(self):
        with patch.dict("os.environ", {"HOME_ASSISTANT_SENSOR_NAMES": 'broken'}), patch.dict("sys.modules", {
                "home_assistant_api": SimpleNamespace(link=SimpleNamespace(snapshot=lambda: {
                    "environment": [], "received_at": None, "age_seconds": None}))}):
            self.assertIn("error", self.poll.refresh(SENSOR))
            self.sdk.get_hub_sensor.assert_not_called()


class UnifiedLinkTests(unittest.TestCase):
    setUp = link_tests.HomeAssistantTests.setUp

    def test_sensor_protocol_reconnect_unknown_and_light_not_lux(self):
        reading = {"id": "c" * 32, "entity_id": "sensor.level", "name": "Hub 2", "kind": "light_level", "value": 11, "available": True}
        with patch.dict("sys.modules", {"ha_sensor_events": SimpleNamespace(notify=Mock())}):
            with self.client.websocket_connect("/api/home-assistant/ws") as ws:
                ws.send_json({"type": "hello", "protocol": 1, "token": HA})
                self.assertIn("environment", ws.receive_json()["capabilities"])
                ws.send_json({"type": "snapshot", "sequence": 1, "observations": [], "environment": [reading]})
                self.assertTrue(ws.receive_json()["accepted"])
                self.assertEqual(self.api.link.snapshot()["environment"][0]["value"], 11)
            self.assertIsNone(self.api.link.snapshot()["environment"][0]["value"])
        for value in [0, 21, True, "11", float("inf")]:
            with self.assertRaises(ValueError):
                self.api.EnvironmentReading.model_validate({**reading, "value": value})
        for extra in [reading, {**reading, "id": "d" * 32, "entity_id": "sensor.other"}]:
            with self.assertRaises(ValueError):
                self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [], "environment": [reading, extra]})

    def test_hue_uses_ha_and_offline_never_uses_pc(self):
        import lighting_transport
        async def scenario():
            socket = SimpleNamespace()
            state = self.api.link
            async def send(frame):
                self.assertEqual(frame["type"], "hue_command")
                state.hue_result(socket, self.api.HueResult.model_validate({"type": "hue_result", "request_id": frame["request_id"], "status": "success", "result": {"areas": []}}))
            socket.send_json = send
            state.connect(socket, hue_capable=True)
            state.receive(socket, self.api.Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": []}))
            result = await lighting_transport.send_command("hue.list_areas", {})
            self.assertEqual(result, {"status": "ok", "agent_id": "home_assistant", "result": {"areas": []}})
            state.disconnect(socket)
            self.assertEqual((await lighting_transport.send_command("hue.list_areas", {}))["status"], "error")
        pc = AsyncMock(side_effect=AssertionError("No PC fallback"))
        with patch.dict("os.environ", {"HOME_ASSISTANT_HUE_ENABLED": "true"}), patch.dict("sys.modules", {
                "home_assistant_api": self.api, "agent_ws": SimpleNamespace(send_agent_command=pc)}):
            asyncio.run(scenario())
        pc.assert_not_called()

    def test_legacy_hue_route_remains_opt_in_compatible(self):
        import lighting_transport
        pc = AsyncMock(return_value={"status": "ok"})
        with patch.dict("os.environ", {"HOME_ASSISTANT_HUE_ENABLED": "false"}), patch.dict("sys.modules", {"agent_ws": SimpleNamespace(send_agent_command=pc)}):
            asyncio.run(lighting_transport.send_command("hue.list_areas", {}))
        pc.assert_awaited_once()

    def test_legacy_reminder_queue_disabled_and_private_text_not_sent_to_ha(self):
        import lighting_reminders
        command = Mock()
        queue = Mock(return_value={"reminders": [{"item": "private todo", "person": "private person", "light_area_id": "group", "light_area_resource_type": "grouped_light"}]})
        with patch.dict("os.environ", {"HOME_ASSISTANT_HUE_ENABLED": "true"}), patch.dict("sys.modules", {
                "home_assistant_api": SimpleNamespace(link=SimpleNamespace(snapshot=lambda: {"hue_available": True})),
                "web_api": SimpleNamespace(collect_todo_light_reminders=queue),
                "lighting_transport": SimpleNamespace(send_command_sync=command, ha_enabled=lambda: True)}), patch.object(lighting_reminders, "_attempted", {}):
            legacy = endpoint("web_api.py", "api_get_todo_light_reminders", {"collect_todo_light_reminders": queue})
            self.assertEqual(legacy()["reminders"], [])
            queue.assert_not_called()
            lighting_reminders.tick()
            lighting_reminders.tick()
            command.assert_called_once_with("hue.breathe", {"resource_id": "group", "resource_type": "grouped_light"})
            self.assertNotIn("private", json.dumps(command.call_args.args))
