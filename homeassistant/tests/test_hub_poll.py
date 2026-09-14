"""Real HA lifecycle/coordinator tests with only the timer clock and SDK faked."""
import asyncio
from datetime import datetime, timezone
from logging import getLogger
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.debounce import Debouncer

from custom_components.switchbot_hub_light import push
from custom_components.switchbot_hub_light.const import poll_interval
from test_hub_light import setup_hub, cleanup_provider
from test_hub_push import hint, DEVICE

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "cleanup_provider")


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1000
        timers = []

        def schedule(self, hass, delay, callback):
            timer = {"at": self.now + delay, "callback": callback, "active": True}
            self.timers.append(timer)
            return lambda: timer.update(active=False)

        def advance(self, seconds):
            self.now += seconds
            for timer in list(self.timers):
                if timer["active"] and timer["at"] <= self.now:
                    timer["active"] = False
                    timer["callback"](datetime.now(timezone.utc))

        @property
        def active(self):
            return [timer for timer in self.timers if timer["active"]]

    clock = Clock()
    monkeypatch.setattr(push, "monotonic", lambda: clock.now)
    monkeypatch.setattr(push, "async_call_later", clock.schedule)
    return clock


async def setup(hass):
    result = await setup_hub(hass, Debouncer(hass, getLogger(__name__), cooldown=0, immediate=True))
    return (*result, hass.data["switchbot_hub_light"][result[2].entry_id])


async def drain(hass, manager):
    await asyncio.wait_for(asyncio.gather(*list(manager.tasks.values())), 3)
    await hass.async_block_till_done()


async def test_local_timer_reads_one_native_sample_and_unload_cancels(hass, clock):
    native, source, entry, coordinator, own, manager = await setup(hass)
    coordinator.update_method = AsyncMock(return_value={"temperature": 29, "humidity": 60, "lightLevel": 2})
    assert "home_butler" not in hass.data  # Works while Render/relay is absent.
    assert len(clock.active) == 1
    clock.advance(59)
    coordinator.update_method.assert_not_awaited()
    clock.advance(1)
    await drain(hass, manager)
    coordinator.update_method.assert_awaited_once()
    assert coordinator.data == {"temperature": 29, "humidity": 60, "lightLevel": 2}
    assert hass.states.get(own.entity_id).state == "2"
    attrs = hass.states.get(own.entity_id).attributes
    assert attrs["poll_interval_seconds"] == 60
    assert "last_poll_refresh_requested" in attrs and "last_push_refresh_requested" not in attrs
    clock.advance(60)
    assert manager.tasks
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert manager.closed and not manager.tasks and not manager.pending and not clock.active
    clock.advance(600)
    coordinator.update_method.assert_awaited_once()


async def test_push_resets_backup_deadline_and_pending_poll_merges_with_push(hass, clock):
    native, source, entry, coordinator, own, manager = await setup(hass)
    coordinator.update_method = AsyncMock(return_value={"lightLevel": 5})
    clock.advance(59)
    manager.receive(hint())
    await drain(hass, manager)
    clock.advance(1)
    assert not manager.tasks  # No extra poll one second after Push.
    clock.advance(58)
    assert not manager.tasks
    clock.advance(1)
    assert manager.tasks
    manager.receive(hint())  # Hint arriving before scheduled read is absorbed.
    await drain(hass, manager)
    assert coordinator.update_method.await_count == 2
    assert len(clock.active) == 1


async def test_hint_during_poll_keeps_last_edge_without_overlapping(hass, clock, monkeypatch):
    native, source, entry, coordinator, own, manager = await setup(hass)
    monkeypatch.setattr(push, "MIN_REFRESH_SECONDS", 0)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def read():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return {"lightLevel": calls}

    coordinator.update_method = read
    clock.advance(60)
    await asyncio.wait_for(started.wait(), 2)
    for _ in range(10):
        manager.receive(hint())
    clock.advance(60)
    assert calls == 1 and len(manager.tasks) == 1
    release.set()
    await drain(hass, manager)
    assert calls == 2 and hass.states.get(own.entity_id).state == "2"
    assert len(clock.active) == 1


async def test_failure_retries_next_interval_and_native_reload_is_resolved(hass, clock):
    native, source, entry, coordinator, own, manager = await setup(hass)
    original = coordinator.async_request_refresh
    coordinator.async_request_refresh = AsyncMock(side_effect=RuntimeError("offline"))
    clock.advance(60)
    await drain(hass, manager)
    coordinator.async_request_refresh.assert_awaited_once()
    assert "last_poll_refresh_requested" not in manager.attributes(source.id)
    clock.advance(59)
    assert not manager.tasks
    native.mock_state(hass, ConfigEntryState.NOT_LOADED)
    clock.advance(1)
    assert not manager.tasks and len(clock.active) == 1
    coordinator.async_request_refresh = original
    coordinator.update_method = AsyncMock(return_value={"lightLevel": 12})
    native.mock_state(hass, ConfigEntryState.LOADED)
    clock.advance(60)
    await drain(hass, manager)
    coordinator.update_method.assert_awaited_once()
    assert hass.states.get(own.entity_id).state == "12"


async def test_interval_options_validate_persist_and_replace_timer(hass, clock):
    native, source, entry, coordinator, own, manager = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id,
        data={"hub_sensors": [source.entity_id], "poll_interval": 59})
    assert result["errors"]["base"] == "invalid_poll_interval"
    result = await hass.config_entries.options.async_configure(result["flow_id"],
        user_input={"hub_sensors": [source.entity_id], "poll_interval": 120})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    replacement = hass.data["switchbot_hub_light"][entry.entry_id]
    assert entry.options["poll_interval"] == 120
    assert manager.closed and replacement.interval == 120 and len(clock.active) == 1
    assert hass.states.get(own.entity_id).attributes["poll_interval_seconds"] == 120
    clock.advance(60)
    assert not replacement.tasks
    coordinator.update_method = AsyncMock(return_value={"lightLevel": 6})
    clock.advance(60)
    await drain(hass, replacement)
    coordinator.update_method.assert_awaited_once()


def test_interval_bounds():
    for value in (None, True, "60", 0, 59, 60.5, 3601, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            poll_interval(value)
    assert poll_interval(60.0) == 60
    assert poll_interval(3600) == 3600
