"""Run with the real HA Core framework on Linux, with only backend I/O mocked."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_butler.const import DOMAIN
from custom_components.home_butler.observations import current_entity_ids, project_state, select_sources, snapshot
from custom_components.home_butler.config_flow import export_selector
from custom_components.home_butler.transport import AuthError, LinkError, normalize_url

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def add_sensor(hass, domain="binary_sensor", device_class="occupancy", unique_id="fp2"):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(domain, "test", unique_id)
    attrs = {"device_class": device_class, "friendly_name": "客廳"}
    if domain == "sensor":
        attrs["unit_of_measurement"] = "lx"
    hass.states.async_set(entry.entity_id, "on" if domain == "binary_sensor" else "0", attrs)
    return entry


async def test_selection_rename_removal_and_unknown(hass):
    entry = add_sensor(hass)
    add_sensor(hass, unique_id="private_unselected")
    sources = select_sources(hass, [entry.entity_id])
    assert len(snapshot(hass, sources)) == 1
    assert snapshot(hass, sources)[0]["value"] is True
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, new_entity_id="binary_sensor.renamed")
    hass.states.async_set("binary_sensor.renamed", "off", {"device_class": "occupancy"})
    assert current_entity_ids(hass, sources) == ["binary_sensor.renamed"]
    result = snapshot(hass, sources)[0]
    assert result["id"] == entry.id and result["value"] is False
    hass.states.async_set("binary_sensor.renamed", "unavailable", {"device_class": "occupancy"})
    assert snapshot(hass, sources)[0]["value"] is None
    registry.async_remove("binary_sensor.renamed")
    assert current_entity_ids(hass, sources) == []
    assert snapshot(hass, sources)[0]["available"] is False


async def test_lux_and_whitelist(hass):
    entry = add_sensor(hass, "sensor", "illuminance")
    source = select_sources(hass, [entry.entity_id])[0]
    assert snapshot(hass, [source])[0]["value"] == 0
    for value in ("unknown", "unavailable", "nan", "-1", "inf"):
        assert project_state(source, State(entry.entity_id, value,
               {"device_class": "illuminance", "unit_of_measurement": "lx"}))["value"] is None
    with pytest.raises(ValueError):
        select_sources(hass, [entry.entity_id, entry.entity_id])
    wrong = add_sensor(hass, "sensor", "temperature", "temp")
    with pytest.raises(ValueError):
        select_sources(hass, [wrong.entity_id])
    assert export_selector()([entry.entity_id]) == [entry.entity_id]


async def test_config_flow_setup_options_and_unload(hass):
    sensor = add_sensor(hass)
    with patch("custom_components.home_butler.config_flow.validate_connection", new=AsyncMock()):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] == "form"
        with patch("custom_components.home_butler.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {
                "url": "https://example.invalid", "api_key": "x" * 40, "export_entities": [sensor.entity_id]})
            await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data["sources"][0]["id"] == sensor.id
    # Exercise the real setup/unload lifecycle separately from flow auto-setup.
    from custom_components.home_butler import async_setup_entry, async_unload_entry
    with patch("custom_components.home_butler.validate_connection", new=AsyncMock()), +         patch("custom_components.home_butler.OutboundLink.run", new=AsyncMock(side_effect=lambda: None)):
        assert await async_setup_entry(hass, entry)
        assert await async_unload_entry(hass, entry)


async def test_auth_and_connection_failure_are_distinct(hass):
    for error, expected in ((AuthError(), "invalid_auth"), (LinkError(), "cannot_connect")):
        with patch("custom_components.home_butler.config_flow.validate_connection", new=AsyncMock(side_effect=error)):
            result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER},
                data={"url": "https://example.invalid", "api_key": "x" * 40, "export_entities": []})
        assert result["errors"]["base"] == expected
    for url in ("http://example.com", "https://user:key@example.com", "https://example.com/path"):
        with pytest.raises(ValueError):
            normalize_url(url)
