"""SwitchBot's webhook, passed to Home Assistant as a hint to re-read a Hub 2 sooner.

TO DO (docs/refactor-plan.md): remove this module once HA has its own public address and SwitchBot can
notify it directly. It lives here only because SwitchBot needs a public URL and HA has none.

SwitchBot's webhooks are unsigned, so nothing in them is trusted: only "this Hub 2 changed" crosses to
HA (never the light level), for hubs HA asked about, at most once every 3 seconds per hub, and a
replayed or stale notice is ignored. Even a forged one can only cause one bounded re-read.
"""
from __future__ import annotations

import asyncio
import re
import time

MIN_GAP_SECONDS = 3


def hub_changed(body, now: float | None = None) -> str | None:
    """The Hub 2's id if this is a recent light-level change report from one, else None."""
    if not isinstance(body, dict) or body.get("eventType") != "changeReport":
        return None
    context = body.get("context")
    if not isinstance(context, dict) or context.get("deviceType") not in ("WoHub2", "Hub 2"):
        return None
    device = context.get("deviceMac")
    if not isinstance(device, str):
        return None
    device = device.replace(":", "").replace("-", "").upper()
    level, sampled = context.get("lightLevel"), context.get("timeOfSample")
    if not re.fullmatch(r"[0-9A-F]{12}", device) or type(level) is not int or not 1 <= level <= 20:
        return None
    if type(sampled) not in (int, float):
        return None
    if sampled > 100_000_000_000:
        sampled /= 1000                        # milliseconds
    if not -30 <= (time.time() if now is None else now) - sampled <= 120:
        return None
    return device


class HubRelay:
    def __init__(self, link, clock=time.monotonic):
        self.link, self.clock = link, clock
        self.last_sent: dict[str, float] = {}
        self.pending: dict[str, float] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    async def notify(self, body) -> bool:
        device = hub_changed(body)
        with self.link.lock:
            if device is None or device not in self.link.hub_devices or not self.link.can("hub_updates"):
                return False
            self.pending[device] = time.time()
            if device not in self.tasks:
                self.tasks[device] = asyncio.create_task(self._send(device, self.link.socket))
            return True

    async def _send(self, device, socket):
        try:
            while device in self.pending:
                await asyncio.sleep(max(0, MIN_GAP_SECONDS - (self.clock() - self.last_sent.get(device, -1e9))))
                with self.link.lock:
                    if socket is not self.link.socket or device not in self.link.hub_devices or not self.link.can("hub_updates"):
                        return
                    received = self.pending.pop(device)
                    self.last_sent[device] = self.clock()
                await asyncio.wait_for(socket.send_json({"type": "hub_update", "device_id": device, "received_at": received}), 2)
        except (Exception, asyncio.CancelledError):
            pass        # no queue, replay or retry while HA is away
        finally:
            if self.tasks.get(device) is asyncio.current_task():
                self.tasks.pop(device, None)
                self.pending.pop(device, None)


API = "https://api.switch-bot.com/v1.1"


def signed_headers(token: str, secret: str, now_ms: int | None = None, nonce: str | None = None) -> dict:
    import base64, hashlib, hmac, uuid
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    nonce = nonce or str(uuid.uuid4())
    sign = base64.b64encode(hmac.new(secret.encode(), f"{token}{now_ms}{nonce}".encode(), hashlib.sha256).digest()).decode()
    return {"Authorization": token, "sign": sign, "nonce": nonce, "t": str(now_ms), "Content-Type": "application/json; charset=utf8"}


def register(url: str, token: str, secret: str, client) -> str:
    """Point the account's one webhook at url. Returns what was done: already | updated | created."""
    def call(path, body):
        reply = client.post(f"{API}/webhook/{path}", json=body, headers=signed_headers(token, secret), timeout=10)
        data = reply.json()
        return data.get("statusCode") == 100, data.get("body") or {}
    ok, body = call("queryWebhook", {"action": "queryUrl"})
    urls = (body.get("urls") or []) if ok else []
    if url in urls:
        return "already"
    if urls:
        ok, _ = call("updateWebhook", {"action": "updateWebhook", "config": {"url": url, "enable": True}})
        if not ok:
            raise RuntimeError("SwitchBot refused the webhook update")
        return "updated"
    ok, _ = call("setupWebhook", {"action": "setupWebhook", "url": url, "deviceList": "ALL"})
    if not ok:
        raise RuntimeError("SwitchBot refused the webhook setup")
    return "created"
