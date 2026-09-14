"""Push hints and a local backup timer share authenticated native Hub reads."""
import asyncio
from datetime import datetime, timezone
import re
import time
from time import monotonic
from logging import getLogger

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .source import resolve
from .const import DEFAULT_POLL_INTERVAL, poll_interval

UPDATE_SIGNAL = "home_butler_hub_update"
SOURCES_SIGNAL = "home_butler_hub_sources"
DIAGNOSTIC_SIGNAL = "switchbot_hub_light_push_diagnostic"
MIN_REFRESH_SECONDS = 10
_LOGGER = getLogger(__name__)


class PushRefresh:
    def __init__(self, hass, sources, interval=DEFAULT_POLL_INTERVAL):
        self.hass, self.sources = hass, sources
        self.interval = poll_interval(interval)
        self.tasks, self.pending, self.last_attempt = {}, {}, {}
        self.received, self.refreshed = {}, {}
        self.polled = {}
        self.closed = False
        self._started = None
        self._cancel_poll = None

    @callback
    def start(self):
        # No immediate setup I/O. This timer is local and needs no Render link.
        if self._started is None and not self.closed:
            self._started = monotonic()
            self._arm_poll()

    @callback
    def _arm_poll(self):
        if self._cancel_poll:
            self._cancel_poll()
            self._cancel_poll = None
        if self.closed or self._started is None:
            return
        now = monotonic()
        delays = [self.last_attempt.get(device, self._started) + self.interval - now
                  for device in self.selected() if device not in self.tasks]
        # Re-resolve unavailable/reloaded native sources on the next interval.
        self._cancel_poll = async_call_later(
            self.hass, max(1, min(delays, default=self.interval)), self._poll_due)

    @callback
    def _poll_due(self, _now):
        self._cancel_poll = None
        if self.closed:
            return
        now = monotonic()
        for device in self.selected():
            if (device not in self.tasks
                    and now - self.last_attempt.get(device, self._started) >= self.interval):
                self._queue(device, "poll")
        self._arm_poll()

    @callback
    def _queue(self, device, reason):
        self.pending[device] = reason
        if device not in self.tasks:
            self.tasks[device] = self.hass.async_create_background_task(self._refresh(device), "Hub 2 refresh")

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
        self.received[device] = datetime.now(timezone.utc).isoformat()
        self._queue(device, "push")

    async def _refresh(self, device):
        try:
            while not self.closed and self.pending.get(device):
                await asyncio.sleep(max(0.4, MIN_REFRESH_SECONDS - (monotonic() - self.last_attempt.get(device, -float("inf")))))
                reason = self.pending.pop(device, None)
                current = self.selected().get(device)
                if current is None:
                    return
                source_id, coordinator = current
                self.last_attempt[device] = monotonic()
                try:
                    await coordinator.async_request_refresh()
                except Exception:
                    # Native coordinators normally report failures themselves.
                    # An unexpected provider error must not stop future polls.
                    _LOGGER.warning("Hub 2 native refresh request failed")
                    continue
                finally:
                    # Long/failed requests must not cause catch-up bursts.
                    self.last_attempt[device] = monotonic()
                if coordinator.last_update_success:
                    stamps = self.refreshed if reason == "push" else self.polled
                    stamps[device] = datetime.now(timezone.utc).isoformat()
                async_dispatcher_send(self.hass, DIAGNOSTIC_SIGNAL, source_id)
        finally:
            self.tasks.pop(device, None)
            self._arm_poll()

    def attributes(self, source_id):
        for device, (selected_id, _) in self.selected().items():
            if selected_id == source_id:
                return {key: value for key, value in {
                    "last_push_received": self.received.get(device),
                    "last_push_refresh_requested": self.refreshed.get(device),
                    "last_poll_refresh_requested": self.polled.get(device),
                    "poll_interval_seconds": self.interval,
                }.items() if value is not None}
        return {}

    async def close(self):
        self.closed = True
        if self._cancel_poll:
            self._cancel_poll()
            self._cancel_poll = None
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.pending.clear()
