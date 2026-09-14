"""Explicit sensor mapping to existing HB names; registry identity survives renames.

Values are HA state values in canonical units, with no HB calibration applied here.
Hub light levels are deliberately separate from illuminance (lux).
"""
import math
from homeassistant.helpers import entity_registry as er

KINDS = {
    "temperature": ("temperature", "°C", -100, 150),
    "humidity": ("humidity", "%", 0, 100),
    "co2": ("carbon_dioxide", "ppm", 0, 100000),
    "light_level": (None, None, 1, 20),
}


def valid_source(entry, state, kind):
    if not entry or entry.disabled_by is not None or entry.domain != "sensor" or not state or kind not in KINDS:
        return False
    device_class, unit, _, _ = KINDS[kind]
    if kind == "light_level":
        return entry.platform == "switchbot_hub_light"
    return (state.attributes.get("device_class") == device_class
            and state.attributes.get("unit_of_measurement") == unit)


def select_environment(hass, mapping):
    if not isinstance(mapping, dict) or len(mapping) > 20:
        raise ValueError("Invalid sensor mapping")
    registry = er.async_get(hass)
    result, used = [], set()
    for name, metrics in mapping.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 160:
            raise ValueError("Use the exact HB sensor name")
        if not isinstance(metrics, dict) or not metrics or set(metrics) - KINDS.keys():
            raise ValueError("Invalid sensor metrics")
        for kind, entity_id in metrics.items():
            if not isinstance(entity_id, str):
                raise ValueError("Invalid sensor entity")
            entry = registry.async_get(entity_id)
            if not valid_source(entry, hass.states.get(entity_id), kind) or entry.id in used:
                raise ValueError("Select unique sensors with matching units")
            used.add(entry.id)
            result.append({"id": entry.id, "entity_id": entity_id, "name": name, "kind": kind})
    return result


def current_mapping(hass, sources):
    registry = er.async_get(hass)
    by_id = {entry.id: entry for entry in registry.entities.values()}
    result = {}
    for source in sources:
        entry = by_id.get(source["id"])
        result.setdefault(source["name"], {})[source["kind"]] = (
            entry.entity_id if entry else source["entity_id"])
    return result


def snapshot(hass, sources):
    registry = er.async_get(hass)
    by_id = {entry.id: entry for entry in registry.entities.values()}
    result = []
    for source in sources:
        entry = by_id.get(source["id"])
        entity_id = entry.entity_id if entry else source["entity_id"]
        state = hass.states.get(entity_id) if entry else None
        value = None
        if valid_source(entry, state, source["kind"]):
            try:
                number = float(state.state)
                _, _, low, high = KINDS[source["kind"]]
                if math.isfinite(number) and low <= number <= high and (source["kind"] != "light_level" or number.is_integer()):
                    value = number
            except (ValueError, TypeError):
                pass
        result.append({**source, "entity_id": entity_id, "value": value,
                       "available": value is not None,
                       "source_updated_at": state.last_updated.timestamp() if state else None})
    return result
