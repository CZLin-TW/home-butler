"""Unsigned SwitchBot webhooks are refresh hints, never HA sensor values."""
import asyncio
import re
import time


def hub_hint(body, now=None):
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
    # SwitchBot samples may use epoch seconds or milliseconds. Ignore replayed
    # notifications; a current Cloud read is still required at the HA endpoint.
    if sampled > 100_000_000_000:
        sampled /= 1000
    if not -30 <= (time.time() if now is None else now) - sampled <= 120:
        return None
    return device


class HubRelay:
    def __init__(self, link):
        self.link = link
        self.reset(False)

    def reset(self, enabled):
        for task in getattr(self, "tasks", {}).values():
            task.cancel()
        self.enabled = enabled
        self.devices = set()
        self.last_sent = {}
        self.tasks, self.pending = {}, {}

    def select(self, devices):
        self.devices = set(devices) if self.enabled else set()
        self.last_sent = {key: value for key, value in self.last_sent.items() if key in self.devices}
        for device, task in list(self.tasks.items()):
            if device not in self.devices:
                task.cancel()

    async def notify(self, body):
        device = hub_hint(body)
        with self.link.lock:
            if device not in self.devices or not self.link.snapshot()["online"]:
                return False
            socket = self.link.socket
            self.pending[device] = time.time()
            if device not in self.tasks:
                self.tasks[device] = asyncio.create_task(self._send(device, socket))
            return True

    async def _send(self, device, socket):
        try:
            while device in self.pending:
                await asyncio.sleep(max(0, 3 - (time.monotonic() - self.last_sent.get(device, -float("inf")))))
                if socket is not self.link.socket or device not in self.devices or not self.link.snapshot()["online"]:
                    return
                received = self.pending.pop(device)
                self.last_sent[device] = time.monotonic()
                # No lightLevel or arbitrary attributes cross the link. Even a
                # forged webhook can only request a bounded read of a selected hub.
                await asyncio.wait_for(socket.send_json({"type": "hub_update", "device_id": device,
                                                         "received_at": received}), 2)
        except (Exception, asyncio.CancelledError):
            pass  # No queue, replay, or retry when HA is disconnected.
        finally:
            if self.tasks.get(device) is asyncio.current_task():
                self.tasks.pop(device, None)
                self.pending.pop(device, None)
