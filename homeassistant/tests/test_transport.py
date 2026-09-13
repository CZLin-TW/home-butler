import asyncio
from unittest.mock import patch

import aiohttp

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
