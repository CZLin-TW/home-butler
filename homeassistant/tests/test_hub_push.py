import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.debounce import Debouncer
from logging import getLogger

from custom_components.switchbot_hub_light.push import UPDATE_SIGNAL
from custom_components.home_butler.hub_updates import devices
from test_hub_light import setup_hub, cleanup_provider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "cleanup_provider")
DEVICE = "AABBCCDDEEFF"


def hint(**kwargs):
    return {"type": "hub_update", "device_id": DEVICE, "received_at": time.time(), **kwargs}


async def test_push_uses_authenticated_read_not_payload_and_ignores_unknown_stale(hass):
    native, source, entry, coordinator, own = await setup_hub(hass)
    manager = hass.data["switchbot_hub_light"][entry.entry_id]
    assert devices(hass) == [DEVICE]
    coordinator.update_method = AsyncMock(return_value={"temperature": 28, "lightLevel": 3})
    for frame in (hint(device_id="112233445566"), hint(received_at=1), hint(received_at=True), hint(received_at=float("nan"))):
        async_dispatcher_send(hass, UPDATE_SIGNAL, frame)
    await hass.async_block_till_done()
    assert not manager.tasks
    coordinator.update_method.assert_not_awaited()
    async_dispatcher_send(hass, UPDATE_SIGNAL, hint(lightLevel=1))
    await hass.async_block_till_done()
    await asyncio.gather(*list(manager.tasks.values()))
    await hass.async_block_till_done()
    coordinator.update_method.assert_awaited_once()
    state = hass.states.get(own.entity_id)
    assert state.state == "3"  # Arbitrary push value never becomes sensor state.
    assert "last_push_received" in state.attributes
    assert "last_push_refresh_requested" in state.attributes
    assert hass.states.get(source.entity_id).state == "28"


async def test_last_hint_during_refresh_is_not_lost_and_unload_cancels(hass, monkeypatch):
    native, source, entry, coordinator, own = await setup_hub(hass)
    manager = hass.data["switchbot_hub_light"][entry.entry_id]
    monkeypatch.setattr("custom_components.switchbot_hub_light.push.MIN_REFRESH_SECONDS", 0)
    coordinator._debounced_refresh = Debouncer(hass, getLogger(__name__), cooldown=0, immediate=True,
                                              function=coordinator.async_refresh)
    started, release = asyncio.Event(), asyncio.Event()
    reads = 0
    async def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            await release.wait()
            return {"lightLevel": 10}
        return {"lightLevel": 1}
    coordinator.update_method = read
    manager.receive(hint())
    await asyncio.wait_for(started.wait(), 2)
    for _ in range(20):
        manager.receive(hint())
    assert len(manager.tasks) == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(*list(manager.tasks.values())), 3)
    await hass.async_block_till_done()
    assert reads == 2
    assert hass.states.get(own.entity_id).state == "1"
    manager.receive(hint())
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not manager.pending and not manager.tasks
    assert devices(hass) == []
    manager.receive(hint())
    assert not manager.tasks
