"""Machines on the home network whose health HA reads and passes on to HomeButler with its snapshots.

The one way between home and the cloud is this integration's link (home-butler docs/local-hub-architecture.md):
a machine at home no longer sends to HomeButler itself. Each one runs a small read-only service that answers
`GET /metrics` to HA's address only (home-butler agent/mac_metrics_server.py); this reads it every minute.

Configured as lines of `source=url`, e.g. `mac-mini=http://<address>:8792/metrics`. The source name is how
HomeButler knows the machine; keep it when the address changes.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from urllib.parse import urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)
SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
INTERVAL = 60
TIMEOUT = 15
STALE_AFTER = 300            # a report older than this is not passed on: the card should go grey
MAX_MACHINES = 8


def parse(text: str) -> list[dict]:
    """`source=url` per line -> [{"source", "url"}]. Raises ValueError on anything unexpected."""
    machines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        source, _, url = line.partition("=")
        source, url = source.strip(), url.strip()
        parts = urlsplit(url)
        if not SOURCE.match(source) or parts.scheme != "http" or parts.path != "/metrics" or parts.query or parts.fragment \
                or parts.username or parts.password or not parts.hostname or not parts.port:
            raise ValueError("Computers: one `source=http://<home address>:<port>/metrics` per line")
        address = ipaddress.ip_address(parts.hostname)
        if not address.is_private:
            raise ValueError("Computers: home network addresses only")
        machines.append({"source": source, "url": url})
    if len(machines) > MAX_MACHINES or len({m["source"] for m in machines}) != len(machines):
        raise ValueError("Computers: at most 8, each source once")
    return machines


def unparse(machines: list[dict]) -> str:
    return "\n".join(f"{m['source']}={m['url']}" for m in machines)


class ComputerReader:
    def __init__(self, session: aiohttp.ClientSession, machines: list[dict], notify, clock=time.time):
        self.session, self.machines, self.notify, self.clock = session, machines, notify, clock
        self.latest: dict[str, dict] = {}
        self.failing: set[str] = set()

    async def read_once(self) -> None:
        for machine in self.machines:
            source = machine["source"]
            try:
                async with self.session.get(machine["url"], timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as answer:
                    if answer.status != 200:
                        raise ValueError(f"HTTP {answer.status}")
                    body = await answer.json(content_type=None)
                report = {"source": source, "sampled_at": int(body["sampled_at"]), "heartbeat": body["heartbeat"],
                          "watchdogs": (body.get("extras") or {}).get("watchdogs")}
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                if source not in self.failing:
                    _LOGGER.warning("Computer %s could not be read", source)
                self.failing.add(source)
                continue
            if source in self.failing:
                _LOGGER.info("Computer %s readable again", source)
                self.failing.discard(source)
            previous = self.latest.get(source)
            self.latest[source] = report
            if not previous or previous["sampled_at"] != report["sampled_at"]:
                self.notify()

    async def run(self) -> None:
        while True:
            await self.read_once()
            await asyncio.sleep(INTERVAL)

    def snapshot(self) -> list[dict]:
        now = self.clock()
        return [r for r in self.latest.values() if now - r["sampled_at"] <= STALE_AFTER]
