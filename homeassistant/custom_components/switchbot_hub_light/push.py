"""Webhook hints wake authenticated native reads; no external state injection."""
import asyncio
from datetime import datetime, timezone
import re
import time

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .source import resolve

UPDATE_SIGNAL = "home_butler_hub_update"
SOURCES_SIGNAL = "home_butler_hub_sources"
DIAGNOSTIC_SIGNAL = "switchbot_hub_light_push_diagnostic"
MIN_REFRESH_SECONDS = 10


class PushRefresh:
    def __init__(self, hass, sources):
        self.hass, self.sources = hass, sources
        self.tasks, self.pending, self.last_attempt = {}, {}, {}
        self.received, self.refreshed = {}, {}
        self.closed = False

    def selected(self):
        result = {}
        for source_id in self.sources:
            try:
                source, coordinator = resolve(self.hass, source_id)
                device = source.unique_id.removesuffix("_temperature").replace(":", "").replace("-", "").upper()
                if re.fullmatch(r"[0-9A-F]{12}", device):
                    result[device] = (source_id, coordinator)
            except (ValueError, AttributeError):
                pass
        return result

    @callback
    def receive(self, frame):
        if self.closed or not isinstance(frame, dict) or frame.get("type") != "hub_update":
            return
        device, received = frame.get("device_id"), frame.get("received_at")
        if (not isinstance(device, str) or device not in self.selected()
                or type(received) not in (float, int) or not -30 <= time.time() - received <= 120):
            return
        # Values in an unsigned webhook are deliberately ignored. Repeated
        # hints merge into one pending read, including the last edge in a burst.
        self.pending[device] = True
        self.received[device] = datetime.now(timezone.utc).isoformat()
        if device not in self.tasks:
            self.tasks[device] = self.hass.async_create_background_task(self._refresh(device), "Hub 2 push refresh")

    async def _refresh(self, device):
        try:
            while not self.closed and self.pending.get(device):
                await asyncio.sleep(max(0.4, MIN_REFRESH_SECONDS - (time.monotonic() - self.last_attempt.get(device, -float("inf")))))
                self.pending.pop(device, None)
                current = self.selected().get(device)
                if current is None:
                    return
                source_id, coordinator = current
                self.last_attempt[device] = time.monotonic()
                await coordinator.async_request_refresh()
                if coordinator.last_update_success:
                    self.refreshed[device] = datetime.now(timezone.utc).isoformat()
                async_dispatcher_send(self.hass, DIAGNOSTIC_SIGNAL, source_id)
        finally:
            self.tasks.pop(device, None)

    def attributes(self, source_id):
        for device, (selected_id, _) in self.selected().items():
            if selected_id == source_id:
                return {key: value for key, value in {
                    "last_push_received": self.received.get(device),
                    "last_push_refresh_requested": self.refreshed.get(device),
                }.items() if value is not None}
        return {}

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
