"""Canonical units, registry identity and options with the real HA framework."""
import pytest
from homeassistant.helpers import entity_registry as er
from custom_components.home_butler.environment import select_environment, current_mapping, snapshot


async def test_environment_rename_replacement_and_units(hass):
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("sensor", "switchbot_cloud", "temperature")
    attrs = {"device_class": "temperature", "unit_of_measurement": "°C"}
    hass.states.async_set(sensor.entity_id, "26.5", attrs)
    mapping = {"客廳感測器": {"temperature": sensor.entity_id}}
    sources = select_environment(hass, mapping)
    assert snapshot(hass, sources)[0]["value"] == 26.5
    registry.async_update_entity(sensor.entity_id, new_entity_id="sensor.renamed")
    hass.states.async_set("sensor.renamed", "27", attrs)
    assert current_mapping(hass, sources)["客廳感測器"]["temperature"] == "sensor.renamed"
    assert snapshot(hass, sources)[0]["id"] == sensor.id
    for invalid in ("unavailable", "unknown", "nan", "inf", "200"):
        hass.states.async_set("sensor.renamed", invalid, attrs)
        assert snapshot(hass, sources)[0]["value"] is None
    hass.states.async_set("sensor.renamed", "80", {**attrs, "unit_of_measurement": "°F"})
    assert snapshot(hass, sources)[0]["available"] is False
    registry.async_remove("sensor.renamed")
    replacement = registry.async_get_or_create("sensor", "switchbot_cloud", "replacement", suggested_object_id="renamed")
    hass.states.async_set(replacement.entity_id, "28", attrs)
    assert snapshot(hass, sources)[0]["available"] is False


async def test_environment_zero_and_hub_level_not_lux(hass):
    registry = er.async_get(hass)
    humidity = registry.async_get_or_create("sensor", "switchbot_cloud", "humidity")
    hass.states.async_set(humidity.entity_id, "0", {"device_class": "humidity", "unit_of_measurement": "%"})
    co2 = registry.async_get_or_create("sensor", "switchbot_cloud", "co2")
    hass.states.async_set(co2.entity_id, "850", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    light = registry.async_get_or_create("sensor", "switchbot_hub_light", "light")
    hass.states.async_set(light.entity_id, "12", {"unit_of_measurement": "級"})
    sources = select_environment(hass, {"HB名稱": {"humidity": humidity.entity_id, "co2": co2.entity_id, "light_level": light.entity_id}})
    assert [s["value"] for s in snapshot(hass, sources)] == [0, 850, 12]
    for mapping in ({"HB": {"light_level": humidity.entity_id}}, {"HB": {"illuminance": light.entity_id}},
                    {"HB": {"humidity": humidity.entity_id}, "第二個": {"humidity": humidity.entity_id}}):
        with pytest.raises(ValueError):
            select_environment(hass, mapping)
