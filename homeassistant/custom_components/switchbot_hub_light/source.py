"""Core 2026.9.2 native runtime boundary; never request credentials or new I/O."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er


def resolve(hass, source_id):
    source = er.async_get(hass).async_get(source_id)
    if (not source or source.platform != "switchbot_cloud" or source.domain != "sensor"
            or source.disabled_by or not source.unique_id.endswith("_temperature")):
        raise ValueError("Select a native Hub 2 temperature sensor")
    entry = hass.config_entries.async_get_entry(source.config_entry_id)
    if not entry or entry.state != ConfigEntryState.LOADED:
        raise ValueError("Native integration unavailable")
    runtime = getattr(entry, "runtime_data", None)
    devices = getattr(getattr(runtime, "devices", None), "sensors", ())
    matches = [(device, coordinator) for device, coordinator in devices
               if device.device_type == "Hub 2"
               and source.unique_id == f"{device.device_id}_temperature"]
    if len(matches) != 1:
        raise ValueError("Native Hub 2 not found")
    return source, matches[0][1]


def select_sources(hass, entity_ids):
    if not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 20:
        raise ValueError("Select at least one Hub 2")
    sources = []
    for entity_id in entity_ids:
        source = er.async_get(hass).async_get(entity_id)
        if source is None:
            raise ValueError("Source not registered")
        resolve(hass, source.id)
        sources.append(source.id)
    if len(set(sources)) != len(sources):
        raise ValueError("Duplicate Hub 2")
    return sources


def light_level(data):
    # Official API is an ordinal 1..20 scale, NOT lux or a percentage.
    value = data.get("lightLevel") if isinstance(data, dict) else None
    if type(value) is not int or not 1 <= value <= 20:
        return None
    return value
