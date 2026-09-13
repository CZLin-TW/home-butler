"""Real HA platform and registry; no Render, SwitchBot API, or physical commands."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ac_room_temperature import DOMAIN
from custom_components.ac_room_temperature.config_flow import selected

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def sources(hass):
    registry = er.async_get(hass)
    ac = registry.async_get_or_create("climate", "switchbot_cloud", "living")
    sensor = registry.async_get_or_create("sensor", "test", "room")
    hass.states.async_set(ac.entity_id, "cool", {"friendly_name": "Living AC", "temperature": 28,
        "hvac_modes": ["off", "cool", "heat", "dry", "fan_only", "heat_cool"],
        "fan_mode": "auto", "fan_modes": ["auto", "low", "medium", "high"], "min_temp": 16, "max_temp": 30})
    hass.states.async_set(sensor.entity_id, "26.7", {"device_class": "temperature", "unit_of_measurement": "°C"})
    return ac, sensor


async def setup_pair(hass):
    ac, sensor = sources(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Living AC Room", unique_id=ac.id,
                            data={"source_id": ac.id, "sensor_id": sensor.id})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return ac, sensor, entry, entry.runtime_data


async def test_live_sensor_and_native_changes_publish_without_commands(hass):
    ac, sensor, entry, entity = await setup_pair(hass)
    initial = hass.states.get(entity.entity_id)
    assert initial.attributes["current_temperature"] == 26.7
    assert initial.attributes["temperature"] == 28
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as calls:
        hass.states.async_set(sensor.entity_id, "0", {"device_class": "temperature", "unit_of_measurement": "°C"})
        state = hass.states.get(ac.entity_id)
        hass.states.async_set(ac.entity_id, "heat", {**state.attributes, "temperature": 27})
        await hass.async_block_till_done()
        final = hass.states.get(entity.entity_id)
        assert final.state == "heat" and final.attributes["temperature"] == 27
        assert final.attributes["current_temperature"] == 0
        calls.assert_not_called()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_nan_wrong_units_and_registry_removal_never_display_21(hass):
    ac, sensor, entry, entity = await setup_pair(hass)
    for value, unit in [("unavailable", "°C"), ("unknown", "°C"), ("nan", "°C"), ("inf", "°C"), ("26", "%")]:
        hass.states.async_set(sensor.entity_id, value, {"device_class": "temperature", "unit_of_measurement": unit})
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == "unavailable"
        assert entity.current_temperature is None
    hass.states.async_set(sensor.entity_id, "77", {"device_class": "temperature", "unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).attributes["current_temperature"] == 25
    er.async_get(hass).async_remove(sensor.entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == "unavailable"


async def test_commands_only_relay_to_native_id_without_optimistic_state_or_retry(hass):
    ac, sensor, entry, entity = await setup_pair(hass)
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as calls:
        await entity.async_set_temperature(temperature=26.5, hvac_mode="heat")
        assert calls.call_args.args == ("climate", "set_temperature", {"entity_id": ac.entity_id, "temperature": 27, "hvac_mode": "heat"})
        assert entity.target_temperature == 28 and entity.hvac_mode == "cool"
        for method, value, service, payload in [
            (entity.async_set_hvac_mode, "dry", "set_hvac_mode", {"hvac_mode": "dry"}),
            (entity.async_set_fan_mode, "low", "set_fan_mode", {"fan_mode": "low"}),
        ]:
            await method(value)
            assert calls.call_args.args == ("climate", service, {"entity_id": ac.entity_id, **payload})
        await entity.async_turn_off()
        assert calls.call_args.args == ("climate", "turn_off", {"entity_id": ac.entity_id})
        await entity.async_turn_on()
        assert calls.call_args.args == ("climate", "turn_on", {"entity_id": ac.entity_id})
        calls.reset_mock()
        calls.side_effect = RuntimeError("provider error")
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()
        assert calls.call_count == 1
        calls.reset_mock()
        for value in [True, float("nan"), 40]:
            with pytest.raises(HomeAssistantError):
                await entity.async_set_temperature(temperature=value)
        calls.assert_not_called()


async def test_sensor_options_preserve_identity_and_do_not_control_ac(hass):
    ac, sensor, entry, entity = await setup_pair(hass)
    old_id = entity.entity_id
    new = er.async_get(hass).async_get_or_create("sensor", "test", "second_room_sensor")
    hass.states.async_set(new.entity_id, "25.3", {"device_class": "temperature", "unit_of_measurement": "°C"})
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as calls:
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"temperature_sensor": new.entity_id})
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.runtime_data.entity_id == old_id
        assert hass.states.get(old_id).attributes["current_temperature"] == 25.3
        calls.assert_not_called()


async def test_registry_rename_follows_same_source_and_never_a_replacement(hass):
    ac, sensor, entry, entity = await setup_pair(hass)
    registry = er.async_get(hass)
    old = hass.states.get(ac.entity_id)
    registry.async_update_entity(ac.entity_id, new_entity_id="climate.renamed_native")
    hass.states.async_set("climate.renamed_native", "cool", dict(old.attributes))
    await hass.async_block_till_done()
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as calls:
        await entity.async_turn_off()
        assert calls.call_args.args[2]["entity_id"] == "climate.renamed_native"
        registry.async_remove("climate.renamed_native")
        await hass.async_block_till_done()
        calls.reset_mock()
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()
        calls.assert_not_called()


async def test_flow_rejects_non_native_sources_and_duplicate_pairing(hass):
    ac, sensor = sources(hass)
    imported = er.async_get(hass).async_get_or_create("climate", "homekit_controller", "imported")
    hass.states.async_set(imported.entity_id, "cool")
    with pytest.raises(ValueError):
        selected(hass, imported.entity_id, climate=True)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"climate_entity": ac.entity_id, "temperature_sensor": sensor.entity_id})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"climate_entity": ac.entity_id, "temperature_sensor": sensor.entity_id})
    assert result["type"] == "abort" and result["reason"] == "already_configured"
