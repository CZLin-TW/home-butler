import asyncio
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.home_butler.transport import OutboundLink


async def test_reconnect_reads_current_snapshot_and_resets_sequence():
    value = True
    frames = []
    attempts = 0
    real_sleep = asyncio.sleep

    async def quick_sleep(delay):
        await real_sleep(0)

    class Socket:
        def __init__(self, attempt):
            self.attempt, self.reads = attempt, 0

        async def send_json(self, frame):
            frames.append((self.attempt, frame))

        async def receive_json(self):
            self.reads += 1
            if self.reads == 1:
                return {"type": "hello_ack", "protocol": 1}
            if self.attempt == 1:
                raise aiohttp.ClientConnectionError()
            link.closed = True
            return {"type": "snapshot_ack", "sequence": 1, "accepted": True}

    class Connection:
        async def __aenter__(self):
            nonlocal attempts, value
            attempts += 1
            if attempts == 2:
                value = False
            return Socket(attempts)

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, url, **kwargs):
            assert url == "wss://example.invalid/api/home-assistant/ws"
            return Connection()

    link = OutboundLink(Session(), "https://example.invalid", "x" * 40, lambda: [{"value": value}])
    with patch("custom_components.home_butler.transport.asyncio.sleep", quick_sleep):
        await asyncio.wait_for(link.run(), 2)
    snapshots = [frame for _, frame in frames if frame["type"] == "snapshot"]
    assert len(snapshots) == 2
    assert [frame["sequence"] for frame in snapshots] == [1, 1]
    assert [frame["observations"][0]["value"] for frame in snapshots] == [True, False]
    assert link.connected is False


async def test_climate_link_receives_command_between_snapshots_and_cancels_on_disconnect():
    from unittest.mock import AsyncMock
    from custom_components.home_butler.transport import LinkError
    incoming = asyncio.Queue()
    sent = asyncio.Queue()
    class Socket:
        async def send_json(self, frame):
            await sent.put(frame)
        async def receive_json(self):
            frame = await incoming.get()
            if isinstance(frame, Exception):
                raise frame
            return frame
    commands = AsyncMock()
    commands.execute.return_value = {"type": "climate_result", "status": "success"}
    link = OutboundLink(None, "https://example.invalid", "x" * 40, lambda: [], lambda: [{"name": "AC"}], commands)
    task = asyncio.create_task(link._serve_climates(Socket()))
    try:
        first = await asyncio.wait_for(sent.get(), 2)
        assert first["type"] == "snapshot" and first["climates"] == [{"name": "AC"}]
        await incoming.put({"type": "snapshot_ack", "sequence": 1, "accepted": True})
        frame = {"type": "climate_command", "request_id": "a" * 32}
        await incoming.put(frame)
        result = await asyncio.wait_for(sent.get(), 2)
        assert result["type"] == "climate_result"
        commands.execute.assert_awaited_once_with(frame)
        second = await asyncio.wait_for(sent.get(), 2)
        assert second["sequence"] == 2
        await incoming.put(LinkError("Disconnected"))
        with pytest.raises(LinkError):
            await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_hub_push_between_acknowledgements_without_control_permissions():
    from unittest.mock import Mock
    from custom_components.home_butler.transport import LinkError
    incoming, sent = asyncio.Queue(), asyncio.Queue()
    class Socket:
        async def send_json(self, frame):
            await sent.put(frame)
        async def receive_json(self):
            item = await incoming.get()
            if isinstance(item, Exception):
                raise item
            return item
    updates = Mock()
    link = OutboundLink(None, "https://example.invalid", "x" * 40, lambda: [],
                        hub_devices=lambda: ["AABBCCDDEEFF"], hub_updates=updates)
    link._hub_enabled = True
    task = asyncio.create_task(link._serve_climates(Socket()))
    try:
        first = await asyncio.wait_for(sent.get(), 2)
        assert first["hub_devices"] == ["AABBCCDDEEFF"]
        frame = {"type": "hub_update", "device_id": "AABBCCDDEEFF", "received_at": 1}
        await incoming.put(frame)
        await incoming.put({"type": "snapshot_ack", "sequence": 1, "accepted": True})
        await incoming.put(LinkError("Disconnected"))
        with pytest.raises(LinkError):
            await task
        updates.assert_called_once_with(frame)
        assert link.commands is None and link.ir_commands is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
