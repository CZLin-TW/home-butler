"""Where each shared entity is: its area (the entity's own, else its device's) and that area's floor.

Only a protocol 2 backend receives these; the household is decided by the floor, so a device moved to
another floor in HA moves to the other household without anything else to change."""
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er


def placer(hass):
    def place(items):
        registry, devices, areas = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)
        out = []
        for item in items:
            entry = registry.async_get(item.get("entity_id", ""))
            area_id = entry.area_id if entry else None
            if not area_id and entry and entry.device_id:
                device = devices.async_get(entry.device_id)
                area_id = device.area_id if device else None
            area = areas.async_get_area(area_id) if area_id else None
            out.append({**item, "area": item.get("area") or (area.name if area else "")[:100],
                        "floor_id": ((getattr(area, "floor_id", None) or "") if area else "")[:80]})
        return out
    return place
