"""Real HA sensors and flows sharing a fake native provider's coordinator."""
from logging import getLogger
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.switchbot_hub_light.source import light_level, resolve

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
async def cleanup_provider(hass):
    yield
    for entry in hass.config_entries.async_entries("switchbot_cloud"):
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def setup_hub(hass):
    native = MockConfigEntry(domain="switchbot_cloud", data={}, state=ConfigEntryState.LOADED)
    native.add_to_hass(hass)
    hass.config.components.add("switchbot_cloud")
    coordinator = DataUpdateCoordinator(hass, getLogger(__name__), name="hub", update_method=AsyncMock())
    coordinator.async_set_updated_data({"temperature": 28, "lightLevel": 8})
    native.runtime_data = SimpleNamespace(devices=SimpleNamespace(sensors=[
        (SimpleNamespace(device_type="Hub 2", device_id="hub123"), coordinator)]))
    device = dr.async_get(hass).async_get_or_create(config_entry_id=native.entry_id,
        identifiers={("switchbot_cloud", "hub123")}, name="客廳 Hub 2")
    source = er.async_get(hass).async_get_or_create("sensor", "switchbot_cloud", "hub123_temperature",
        config_entry=native, device_id=device.id, original_device_class="temperature")
    hass.states.async_set(source.entity_id, "28", {"device_class": "temperature", "unit_of_measurement": "°C"})
    entry = MockConfigEntry(domain="switchbot_hub_light", title="Hub 2", unique_id="switchbot_hub_light",
        data={"sources": [source.id]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    own = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0]
    return native, source, entry, coordinator, own


async def test_light_only_update_without_temperature_change_and_no_new_io(hass):
    native, source, entry, coordinator, own = await setup_hub(hass)
    state = hass.states.get(own.entity_id)
    assert state.state == "8"
    assert state.attributes["unit_of_measurement"] == "級"
    assert "device_class" not in state.attributes
    assert own.device_id == source.device_id
    coordinator.async_set_updated_data({"temperature": 28, "lightLevel": 13})
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "13"
    assert hass.states.get(source.entity_id).state == "28"
    coordinator.update_method.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not coordinator._listeners


async def test_missing_invalid_and_disconnected_samples_never_become_zero(hass):
    native, source, entry, coordinator, own = await setup_hub(hass)
    for value in (None, 0, 21, True, "5", 1.5):
        coordinator.async_set_updated_data({"lightLevel": value})
        await hass.async_block_till_done()
        assert hass.states.get(own.entity_id).state == "unknown"
    coordinator.async_set_updated_data({"lightLevel": 20})
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "20"
    coordinator.async_set_update_error(RuntimeError("offline"))
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "unavailable"


async def test_reload_rebinds_coordinator_rename_and_removal(hass):
    native, source, entry, coordinator, own = await setup_hub(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(source.entity_id, new_entity_id="sensor.hub_renamed")
    hass.states.async_remove(source.entity_id)
    hass.states.async_set("sensor.hub_renamed", "unavailable")
    native.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "unavailable"
    replacement = DataUpdateCoordinator(hass, getLogger(__name__), name="replacement")
    replacement.async_set_updated_data({"lightLevel": 17})
    native.runtime_data.devices.sensors[0] = (native.runtime_data.devices.sensors[0][0], replacement)
    native.mock_state(hass, ConfigEntryState.LOADED)
    hass.states.async_set("sensor.hub_renamed", "28")
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "17"
    assert not coordinator._listeners
    assert resolve(hass, source.id)[1] is replacement
    registry.async_remove("sensor.hub_renamed")
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "unavailable"
    assert not replacement._listeners


async def test_config_options_validate_source_and_keep_entity_identity(hass):
    native, source, entry, coordinator, own = await setup_hub(hass)
    result = await hass.config_entries.flow.async_init("switchbot_hub_light", context={"source": "user"})
    assert result["type"] == "abort" and result["reason"] == "already_configured"
    result = await hass.config_entries.options.async_init(entry.entry_id, data={"hub_sensors": []})
    assert result["errors"]["base"] == "invalid_source"
    result = await hass.config_entries.options.async_configure(result["flow_id"],
        user_input={"hub_sensors": [source.entity_id, source.entity_id]})
    assert result["errors"]["base"] == "invalid_source"
    native.runtime_data.devices.sensors[0][0].device_type = "Meter"
    result = await hass.config_entries.options.async_configure(result["flow_id"],
        user_input={"hub_sensors": [source.entity_id]})
    assert result["errors"]["base"] == "invalid_source"
    native.runtime_data.devices.sensors[0][0].device_type = "Hub 2"
    result = await hass.config_entries.options.async_configure(result["flow_id"],
        user_input={"hub_sensors": [source.entity_id]})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get(own.entity_id).state == "8"
