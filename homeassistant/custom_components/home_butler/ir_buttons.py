"""Only locally selected switchbot_ir_buttons entities may be pressed."""
import asyncio
import re
import time
from homeassistant.helpers import entity_registry as er


def identity(hass, entity_id):
    item = er.async_get(hass).async_get(entity_id)
    if not item or item.domain != "button" or item.platform != "switchbot_ir_buttons" or item.disabled_by:
        raise ValueError("Select SwitchBot IR Buttons only")
    config = hass.config_entries.async_get_entry(item.config_entry_id)
    state = hass.states.get(item.entity_id)
    if not config or not state:
        raise ValueError("Button unavailable")
    label = state.attributes.get("ir_button")
    if label not in config.data.get("buttons", []):
        raise ValueError("Unconfigured button")
    return {"id": item.id, "entity_id": item.entity_id,
            "name": config.data["name"], "button": label}


def select_buttons(hass, entities):
    if len(entities) > 120 or len(set(entities)) != len(entities):
        raise ValueError("Too many or repeated buttons")
    sources = [identity(hass, entity_id) for entity_id in entities]
    if len({(s["name"], s["button"]) for s in sources}) != len(sources):
        raise ValueError("Ambiguous button mapping")
    return sources


def snapshot(hass, sources):
    result = []
    for source in sources:
        item = er.async_get(hass).async_get(source["id"])
        available = False
        if item:
            try:
                current = identity(hass, item.entity_id)
                available = (current["name"] == source["name"] and current["button"] == source["button"]
                             and hass.states.get(item.entity_id).state != "unavailable")
            except ValueError:
                pass
        result.append({**source, "entity_id": item.entity_id if item else source["entity_id"], "available": available})
    return result


class IRCommands:
    def __init__(self, hass, sources):
        self.hass, self.sources = hass, sources
        self.lock = asyncio.Lock()
        self.results = {}

    async def execute(self, frame):
        request_id = frame.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request ID")
        result = {"type": "ir_result", "request_id": request_id, "status": "failed"}
        if self.lock.locked():
            return result
        async with self.lock:
            if request_id in self.results:
                old, response = self.results[request_id]
                return response if old == frame else result
            if len(self.results) >= 256:
                self.results.pop(next(iter(self.results)))
            self.results[request_id] = (frame, result)
            if set(frame) != {"type", "request_id", "id", "name", "button", "expires_at"} or frame["type"] != "ir_command":
                return result
            expiry = frame["expires_at"]
            if type(expiry) not in (int, float) or not time.time() < expiry <= time.time() + 30:
                return result
            matches = [s for s in snapshot(self.hass, self.sources)
                       if s["id"] == frame["id"] and s["name"] == frame["name"] and s["button"] == frame["button"] and s["available"]]
            if len(matches) != 1:
                return result
            result["status"] = "unknown"
            try:
                async with asyncio.timeout(22):
                    await self.hass.services.async_call("button", "press", {"entity_id": matches[0]["entity_id"]}, blocking=True)
                result["status"] = "success"
            except Exception:
                pass  # Never replay a relative command after an uncertain outcome.
            return result
