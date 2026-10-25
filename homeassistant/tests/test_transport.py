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


async def test_environment_and_hue_work_without_climate_permissions():
    from unittest.mock import AsyncMock
    incoming, sent = asyncio.Queue(), asyncio.Queue()
    class Socket:
        async def send_json(self, frame):
            await sent.put(frame)
        async def receive_json(self):
            return await incoming.get()
    hue = AsyncMock()
    hue.execute.return_value = {"type": "hue_result", "status": "success", "result": {}}
    link = OutboundLink(None, "https://example.invalid", "x" * 40, lambda: [],
                        environment=lambda: [{"kind": "temperature", "value": 26}], hue_commands=hue)
    task = asyncio.create_task(link._serve_climates(Socket()))
    try:
        first = await asyncio.wait_for(sent.get(), 2)
        assert first["environment"][0]["value"] == 26
        assert first["climates"] == [] and link.commands is None
        await incoming.put({"type": "snapshot_ack", "sequence": 1, "accepted": True})
        command = {"type": "hue_command", "request_id": "a" * 32}
        await incoming.put(command)
        assert (await asyncio.wait_for(sent.get(), 2))["type"] == "hue_result"
        hue.execute.assert_awaited_once_with(command)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_protocol_2_adds_place_to_every_item_and_to_climate_results():
    from unittest.mock import AsyncMock
    from custom_components.home_butler.transport import LinkError
    incoming, sent = asyncio.Queue(), asyncio.Queue()

    class Socket:
        async def send_json(self, frame):
            await sent.put(frame)

        async def receive_json(self):
            frame = await incoming.get()
            if isinstance(frame, Exception):
                raise frame
            return frame
    commands = AsyncMock()
    commands.execute.return_value = {"type": "climate_result", "status": "success", "state": {"entity_id": "climate.ac"}}
    place = lambda items: [{**i, "area": "客廳", "floor_id": "a"} for i in items]
    link = OutboundLink(None, "https://example.invalid", "x" * 40, lambda: [{"entity_id": "binary_sensor.x"}],
                        lambda: [{"entity_id": "climate.ac"}], commands, environment=lambda: [{"entity_id": "sensor.t"}],
                        path="/v2/home-assistant", protocol=2, place=place)
    assert link.url == "wss://example.invalid/v2/home-assistant/ws"
    task = asyncio.create_task(link._serve_climates(Socket()))
    try:
        first = await asyncio.wait_for(sent.get(), 2)
        for key in ("observations", "climates", "environment"):
            assert first[key][0]["floor_id"] == "a", key
        await incoming.put({"type": "snapshot_ack", "sequence": 1, "accepted": True})
        await incoming.put({"type": "climate_command", "request_id": "a" * 32})
        result = await asyncio.wait_for(sent.get(), 2)
        assert result["state"] == {"entity_id": "climate.ac", "area": "客廳", "floor_id": "a"}
        await incoming.put(LinkError("Disconnected"))
        with pytest.raises(LinkError):
            await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    old = OutboundLink(None, "https://example.invalid", "x" * 40, lambda: [], place=place)
    assert old.place is None and old.url.endswith("/api/home-assistant/ws")      # protocol 1 never gets extra fields


async def test_the_rebuilt_backend_is_tried_first_and_the_old_one_still_works():
    from custom_components.home_butler.transport import AuthError, validate_connection
    seen = []

    class Response:
        def __init__(self, status, body):
            self.status, self.body = status, body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return self.body

    def session(answers):
        class Session:
            def get(self, url, headers, **kwargs):
                seen.append((url, headers))
                return Response(*answers[url.split("example.invalid")[1]])
        return Session()
    new = session({"/v2/home-assistant/health": (200, {"protocol": 2})})
    assert await validate_connection(new, "https://example.invalid", "k") == ("/v2/home-assistant", 2)
    assert seen[-1][1] == {"Authorization": "Bearer k"}
    old = session({"/v2/home-assistant/health": (404, {}), "/api/home-assistant/health": (200, {"protocol": 1})})
    assert await validate_connection(old, "https://example.invalid", "k") == ("/api/home-assistant", 1)
    assert seen[-1][1] == {"X-API-Key": "k"}
    with pytest.raises(AuthError):
        await validate_connection(session({"/v2/home-assistant/health": (401, {})}), "https://example.invalid", "k")
