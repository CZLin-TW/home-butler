"""Retired rules cannot execute; authenticated HA refresh/reminders still work."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from test_callback_concurrency import endpoint


class RetirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_client_cannot_enable_or_delete_saved_rules(self):
        # Even enabled historical rows must not be read or written by these routes.
        store = Mock(side_effect=AssertionError("Saved rules must remain untouched"))
        env = {"HTTPException": HTTPException, "lighting_auto": store}
        read = endpoint("lighting_api.py", "api_lighting_auto_rules", env)
        self.assertEqual(await read(), {"rules": {}, "retired": True})
        enable = endpoint("lighting_api.py", "api_set_lighting_auto_rule", env)
        delete = endpoint("lighting_api.py", "api_delete_lighting_auto_rule", env)
        for action in (enable("living", SimpleNamespace(enabled=True)), delete("living")):
            with self.assertRaises(HTTPException) as error:
                await action
            self.assertEqual(error.exception.status_code, 410)
        self.assertFalse(store.mock_calls)

    async def test_webhook_forwards_hint_without_executing_old_rule(self):
        hint = AsyncMock()
        old_rules = Mock(side_effect=AssertionError("Nightlight must never run"))
        handler = endpoint("main.py", "switchbot_webhook", {"lighting_auto": old_rules})
        body = {"eventType": "changeReport", "context": {"deviceMac": "AABBCCDDEEFF", "lightLevel": 1}}
        with patch.dict("sys.modules", {"home_assistant_api": SimpleNamespace(link=SimpleNamespace(hub_updates=SimpleNamespace(notify=hint)))}):
            self.assertEqual(await handler(SimpleNamespace(json=AsyncMock(return_value=body))), {"status": "ok"})
        hint.assert_awaited_once_with(body)
        self.assertFalse(old_rules.mock_calls)

    async def test_reminder_thread_uses_shared_transport_and_unknown_is_not_retried(self):
        import lighting_transport as transport
        command = AsyncMock(side_effect=[{"status": "ok", "result": {"sent": True}}, {"status": "error", "error": "unknown"}])
        with patch.object(transport, "_loop", asyncio.get_running_loop()), patch.object(transport, "send_command", command):
            self.assertEqual(await asyncio.to_thread(transport.send_command_sync, "hue.breathe", {"resource_id": "living"}), {"sent": True})
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                await asyncio.to_thread(transport.send_command_sync, "hue.breathe", {"resource_id": "living"})
        self.assertEqual(command.await_count, 2)
