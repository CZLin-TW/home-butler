"""Outbound-only link; no HA service calls or replayable command queue."""
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
    def __init__(self, session, url, key, snapshot):
        self.session = session
        self.url = normalize_url(url).replace("https://", "wss://", 1) + "/api/home-assistant/ws"
        self.key, self.snapshot = key, snapshot
        self.changed = asyncio.Event()
        self.closed = False
        self.connected = False

    def notify(self):
        self.changed.set()

    async def run(self):
        delay = 5
        failed = False
        while not self.closed:
            try:
                async with self.session.ws_connect(self.url, heartbeat=20, max_msg_size=131072,
                                                    timeout=aiohttp.ClientWSTimeout(ws_receive=45)) as ws:
                    await ws.send_json({"type": "hello", "protocol": PROTOCOL, "token": self.key})
                    hello = await asyncio.wait_for(ws.receive_json(), 15)
                    if not isinstance(hello, dict) or hello.get("type") != "hello_ack" or hello.get("protocol") != PROTOCOL:
                        raise LinkError("Invalid handshake")
                    self.connected = True
                    if failed:
                        _LOGGER.info("HomeButler local link recovered")
                    failed = False
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
