"""Explicit registry-based selection; names and entity IDs may change safely."""
import math

from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er


def select_sources(hass, entity_ids):
    if len(entity_ids) > 128 or len(entity_ids) != len(set(entity_ids)):
        raise ValueError("Select at most 128 distinct sensors")
    registry = er.async_get(hass)
    sources = []
    for entity_id in entity_ids:
        state, entry = hass.states.get(entity_id), registry.async_get(entity_id)
        if state is None or entry is None:
            raise ValueError("Select registered sensors")
        device_class = state.attributes.get("device_class")
        if entity_id.startswith("binary_sensor.") and device_class in ("occupancy", "presence"):
            kind = "occupancy"
        elif entity_id.startswith("sensor.") and device_class == "illuminance" and state.attributes.get("unit_of_measurement") == "lx":
            kind = "illuminance"
        else:
            raise ValueError("Only occupancy and lux sensors can be shared")
        sources.append({"id": entry.id, "entity_id": entity_id, "name": state.name[:160], "kind": kind})
    return sources


def current_entity_ids(hass, sources):
    registry = er.async_get(hass)
    return [entry.entity_id for source in sources
            if (entry := registry.async_get(source["id"])) is not None]


def project_state(source, state, entity_id=None, area=""):
    value = None
    if state is not None:
        device_class = state.attributes.get("device_class")
        if (source["kind"] == "occupancy" and device_class in ("occupancy", "presence")
                and state.state in ("on", "off")):
            value = state.state == "on"
        elif (source["kind"] == "illuminance" and device_class == "illuminance"
              and state.attributes.get("unit_of_measurement") == "lx"):
            try:
                number = float(state.state)
                if math.isfinite(number) and 0 <= number <= 1_000_000:
                    value = number
            except (ValueError, TypeError):
                pass
    return {"id": source["id"], "entity_id": entity_id or source["entity_id"],
            "name": (state.name if state is not None else source["name"])[:160],
            "area": area[:100], "kind": source["kind"], "value": value,
            "available": value is not None,
            "source_updated_at": state.last_updated.timestamp() if state is not None else None}


def snapshot(hass, sources):
    registry, devices, areas = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)
    result = []
    for source in sources:
        entry = registry.async_get(source["id"])
        state = hass.states.get(entry.entity_id) if entry else None
        area_id = entry.area_id if entry else None
        if not area_id and entry and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        area = areas.async_get_area(area_id) if area_id else None
        result.append(project_state(source, state, entry.entity_id if entry else None,
                                    area.name if area else ""))
    return result
