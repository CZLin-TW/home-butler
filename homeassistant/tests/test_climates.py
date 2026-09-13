"""Real HA registry/services; SwitchBot network traffic is replaced by a service."""
import time
from uuid import uuid4

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.home_butler.climates import ClimateCommands, select_climates, snapshot


def ac(hass, platform="switchbot_cloud"):
    entry = er.async_get(hass).async_get_or_create("climate", platform, "living")
    hass.states.async_set(entry.entity_id, "cool", {"friendly_name": "客廳空調", "temperature": 26,
        "hvac_modes": ["off", "cool", "heat", "dry", "fan_only", "heat_cool"],
        "fan_mode": "auto", "fan_modes": ["auto", "low", "medium", "high"], "min_temp": 16, "max_temp": 30})
    return entry


def command(source, patch):
    return {"type": "climate_command", "request_id": uuid4().hex, "id": source["id"],
            "name": source["name"], "patch": patch, "expires_at": time.time() + 15}


async def test_selection_prevents_homebridge_loop_and_keeps_registry_identity(hass):
    entry = ac(hass)
    sources = select_climates(hass, [entry.entity_id])
    old = snapshot(hass, sources)[0]
    assert old["temperature"] == 26 and old["available"]
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, new_entity_id="climate.renamed")
    hass.states.async_set("climate.renamed", "off", {"temperature": 27, "friendly_name": "New name"})
    new = select_climates(hass, ["climate.renamed"], sources)[0]
    assert new["id"] == sources[0]["id"] and new["name"] == "客廳空調"
    assert snapshot(hass, sources)[0]["hvac_mode"] == "off"
    imported = ac(hass, "homekit_controller")
    with pytest.raises(ValueError):
        select_climates(hass, [imported.entity_id])
    registry.async_remove("climate.renamed")
    assert not snapshot(hass, sources)[0]["available"]


async def test_validation_precedes_all_service_calls(hass):
    entry = ac(hass)
    source = select_climates(hass, [entry.entity_id])[0]
    controller = ClimateCommands(hass, [source])
    calls = []
    async def service(call):
        calls.append(call)
    hass.services.async_register("climate", "turn_off", service)
    invalid = [
        {"power": "off", "temperature": 26}, {"power": "on", "temperature": 26.5},
        {"power": "on", "temperature": True}, {"power": "on", "temperature": 31},
        {"power": "on", "mode": "off"}, {"power": "on", "fan_speed": "maximum"},
        {"power": "on", "service": "homeassistant.restart"},
    ]
    for patch in invalid:
        assert (await controller.execute(command(source, patch)))["status"] == "failed"
    for changes in ({"expires_at": time.time() - 1}, {"id": "b" * 32}, {"name": "other"}):
        assert (await controller.execute({**command(source, {"power": "off"}), **changes}))["status"] == "failed"
    assert not calls


async def test_commands_deduplicate_and_return_observed_state(hass):
    entry = ac(hass)
    source = select_climates(hass, [entry.entity_id])[0]
    controller = ClimateCommands(hass, [source])
    calls = []
    async def service(call):
        calls.append(call)
        state = hass.states.get(entry.entity_id)
        hass.states.async_set(entry.entity_id, call.data.get("hvac_mode", state.state),
                              {**state.attributes, "temperature": call.data["temperature"]})
    hass.services.async_register("climate", "set_temperature", service)
    frame = command(source, {"power": "on", "mode": "heat", "temperature": 27})
    result = await controller.execute(frame)
    assert result["status"] == "success" and result["state"]["hvac_mode"] == "heat"
    assert result["state"]["temperature"] == 27
    assert await controller.execute(frame) == result
    assert len(calls) == 1
    assert (await controller.execute({**frame, "patch": {"power": "off"}}))["status"] == "failed"
    assert len(calls) == 1


async def test_service_failure_or_missing_state_confirmation_is_unknown(hass):
    entry = ac(hass)
    source = select_climates(hass, [entry.entity_id])[0]
    controller = ClimateCommands(hass, [source])
    calls = []
    async def failure(call):
        calls.append(call)
        raise RuntimeError("provider-secret-must-not-leak")
    hass.services.async_register("climate", "turn_off", failure)
    frame = command(source, {"power": "off"})
    result = await controller.execute(frame)
    assert result["status"] == "unknown"
    assert "provider-secret" not in str(result)
    await controller.execute(frame)
    assert len(calls) == 1
    async def noop(call):
        pass
    hass.services.async_register("climate", "turn_off", noop)
    assert (await controller.execute(command(source, {"power": "off"})))["status"] == "unknown"
