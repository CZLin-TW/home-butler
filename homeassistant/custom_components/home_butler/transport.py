"""HA-initiated link with selected AC commands; no offline command replay."""
import asyncio
import json
import logging
from urllib.parse import urlsplit

import aiohttp

from .const import HEARTBEAT_SECONDS, PROTOCOL

_LOGGER = logging.getLogger(__name__)


class LinkError(Exception):
    pass


class AuthError(LinkError):
    pass


def normalize_url(value):
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ("", "/")):
        raise ValueError("Use the HTTPS backend origin")
    return value.strip().rstrip("/")


async def validate_connection(session, url, key):
    url = normalize_url(url)
    try:
        async with session.get(url + "/api/home-assistant/health", headers={"X-API-Key": key},
                               timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False) as response:
            if response.status in (401, 403):
                raise AuthError("Invalid HomeButler key")
            if response.status != 200:
                raise LinkError("HomeButler HA endpoint unavailable")
            data = await response.json()
            if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
                raise LinkError("Unsupported protocol")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        raise LinkError("HomeButler connection failed") from None


class OutboundLink:
    def __init__(self, session, url, key, snapshot, climates=None, commands=None, ir_buttons=None, ir_commands=None):
        self.session = session
        self.url = normalize_url(url).replace("https://", "wss://", 1) + "/api/home-assistant/ws"
        self.key, self.snapshot = key, snapshot
        self.changed = asyncio.Event()
        self.closed = False
        self.connected = False
        self.climates, self.commands = climates, commands
        self.ir_buttons, self.ir_commands = ir_buttons, ir_commands

    async def _serve_climates(self, ws):
        """Receive commands while waiting for the next sensor heartbeat."""
        acknowledgements = asyncio.Queue(maxsize=2)
        command_task = None

        async def execute(frame):
            controller = self.ir_commands if frame.get("type") == "ir_command" else self.commands
            response = await controller.execute(frame)
            await ws.send_json(response)
            self.notify()

        async def receive():
            nonlocal command_task
            while not self.closed:
                frame = await ws.receive_json()
                if not isinstance(frame, dict):
                    raise LinkError("Invalid frame")
                if frame.get("type") == "snapshot_ack":
                    if acknowledgements.full():
                        raise LinkError("Unexpected acknowledgement")
                    acknowledgements.put_nowait(frame)
                elif frame.get("type") == "climate_command" or (frame.get("type") == "ir_command" and self.ir_commands):
                    if command_task and not command_task.done():
                        raise LinkError("Concurrent command rejected")
                    if command_task:
                        command_task.result()
                    command_task = asyncio.create_task(execute(frame))
                else:
                    raise LinkError("Unsupported frame")

        async def send():
            sequence = 0
            self.changed.set()
            while not self.closed:
                try:
                    await asyncio.wait_for(self.changed.wait(), HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    pass
                self.changed.clear()
                await asyncio.sleep(0.2)
                sequence += 1
                frame = {"type": "snapshot", "sequence": sequence, "observations": self.snapshot(),
                         "climates": self.climates() if self.climates else []}
                if self.ir_buttons:
                    frame["ir_buttons"] = self.ir_buttons()
                if len(json.dumps(frame).encode("utf-8")) > 131072:
                    raise LinkError("Snapshot too large")
                await ws.send_json(frame)
                ack = await asyncio.wait_for(acknowledgements.get(), 20)
                if ack.get("sequence") != sequence or ack.get("accepted") is not True:
                    raise LinkError("Invalid acknowledgement")
        tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            if command_task:
                tasks.append(command_task)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def notify(self):
        self.changed.set()

    async def run(self):
        delay = 5
        failed = False
        while not self.closed:
            try:
                async with self.session.ws_connect(self.url, heartbeat=20, max_msg_size=131072,
                                                    timeout=aiohttp.ClientWSTimeout(ws_receive=45)) as ws:
                    await ws.send_json({"type": "hello", "protocol": PROTOCOL, "token": self.key,
                                        "climate_control": self.commands is not None, "ir_control": self.ir_commands is not None})
                    hello = await asyncio.wait_for(ws.receive_json(), 15)
                    if not isinstance(hello, dict) or hello.get("type") != "hello_ack" or hello.get("protocol") != PROTOCOL:
                        raise LinkError("Invalid handshake")
                    self.connected = True
                    if failed:
                        _LOGGER.info("HomeButler local link recovered")
                    failed = False
                    if self.commands is not None:
                        if "climate_control" not in hello.get("capabilities", []):
                            raise LinkError("Backend upgrade required")
                        if self.ir_commands and "ir_control" not in hello.get("capabilities", []):
                            raise LinkError("Backend IR upgrade required")
                        await self._serve_climates(ws)
                        continue
                    sequence = 0
                    self.changed.set()  # Fresh full snapshot on every new session.
                    while not self.closed:
                        try:
                            await asyncio.wait_for(self.changed.wait(), HEARTBEAT_SECONDS)
                        except asyncio.TimeoutError:
                            pass
                        self.changed.clear()
                        await asyncio.sleep(0.2)  # coalesce zone transitions
                        sequence += 1
                        frame = {"type": "snapshot", "sequence": sequence, "observations": self.snapshot()}
                        if len(json.dumps(frame).encode("utf-8")) > 131072:
                            raise LinkError("Snapshot too large")
                        await ws.send_json(frame)
                        ack = await asyncio.wait_for(ws.receive_json(), 20)
                        if (not isinstance(ack, dict) or ack.get("type") != "snapshot_ack"
                                or ack.get("sequence") != sequence or ack.get("accepted") is not True):
                            raise LinkError("Snapshot not acknowledged")
                        delay = 5
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, LinkError):
                if not failed:
                    _LOGGER.warning("HomeButler local link unavailable; local HA devices remain independent")
                failed = True
            finally:
                self.connected = False
            if not self.closed:
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)
