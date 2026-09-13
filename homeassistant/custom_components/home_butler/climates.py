"""Selected native SwitchBot climates only. No arbitrary HA service execution."""
import asyncio
import math
import re
import time

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers import entity_registry as er

MODES = {"off", "cool", "heat", "dry", "fan_only", "auto", "heat_cool"}


def select_climates(hass, entity_ids, previous=()):
    if len(entity_ids) > 20 or len(entity_ids) != len(set(entity_ids)):
        raise ValueError("Select at most 20 distinct ACs")
    if entity_ids and hass.config.units.temperature_unit != UnitOfTemperature.CELSIUS:
        raise ValueError("Celsius is required")
    registry = er.async_get(hass)
    sources = []
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        if entry is None or state is None or not entity_id.startswith("climate.") or entry.platform != "switchbot_cloud":
            raise ValueError("Select native SwitchBot Cloud ACs, never Homebridge imports")
        old = next((s for s in previous if s["id"] == entry.id), None)
        sources.append({"id": entry.id, "entity_id": entity_id, "name": old["name"] if old else state.name[:160]})
    if len({s["name"] for s in sources}) != len(sources):
        raise ValueError("AC names must be unique")
    return sources


def snapshot(hass, sources):
    registry = er.async_get(hass)
    states = []
    for source in sources:
        entry = registry.async_get(source["id"])
        state = hass.states.get(entry.entity_id) if entry and entry.platform == "switchbot_cloud" else None
        available = bool(state and state.state in MODES)
        temperature = state.attributes.get("temperature") if available else None
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 5 <= temperature <= 40:
            temperature = None
        fan = state.attributes.get("fan_mode") if available else None
        states.append({"id": source["id"], "entity_id": entry.entity_id if entry else source["entity_id"],
                       "name": source["name"], "available": available,
                       "hvac_mode": state.state if available else None,
                       "temperature": temperature,
                       "fan_mode": fan if isinstance(fan, str) and len(fan) <= 40 else None,
                       "source_updated_at": state.last_updated.timestamp() if state else None})
    return states


class ClimateCommands:
    def __init__(self, hass, sources):
        self.hass, self.sources = hass, sources
        self.results = {}
        self.lock = asyncio.Lock()

    async def execute(self, frame):
        request_id = frame.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request ID")
        result = {"type": "climate_result", "request_id": request_id, "status": "failed", "state": None}
        if self.lock.locked():
            return result
        async with self.lock:
            if request_id in self.results:
                old, response = self.results[request_id]
                return response if old == frame else result
            if len(self.results) >= 256:
                self.results.pop(next(iter(self.results)))
            self.results[request_id] = (frame, result)
            if frame.get("type") != "climate_command" or set(frame) != {"type", "request_id", "id", "name", "patch", "expires_at"}:
                return result
            expiry = frame["expires_at"]
            if type(expiry) not in (int, float) or not time.time() < expiry <= time.time() + 30:
                return result
            source = next((s for s in self.sources if s["id"] == frame["id"] and s["name"] == frame["name"]), None)
            if source is None:
                return result
            entry = er.async_get(self.hass).async_get(source["id"])
            if entry is None or entry.platform != "switchbot_cloud":
                return result
            state = self.hass.states.get(entry.entity_id)
            patch = frame["patch"]
            if (state is None or state.state not in MODES or not isinstance(patch, dict)
                    or set(patch) - {"power", "mode", "temperature", "fan_speed"}
                    or patch.get("power") not in ("on", "off")):
                return result
            mode, temp, fan = patch.get("mode"), patch.get("temperature"), patch.get("fan_speed")
            if mode is not None and (mode == "off" or mode not in state.attributes.get("hvac_modes", [])):
                return result
            if temp is not None and (type(temp) is not int or not max(16, state.attributes.get("min_temp", 16)) <= temp <= min(30, state.attributes.get("max_temp", 30))):
                return result
            if fan is not None and fan not in state.attributes.get("fan_modes", []):
                return result
            if patch["power"] == "off" and patch != {"power": "off"}:
                return result
            # All fields validated before the first side effect. Multiple native
            # service calls can partially succeed, hence failure becomes unknown.
            result["status"] = "unknown"
            async def call(service, data=None):
                await self.hass.services.async_call("climate", service,
                    {"entity_id": entry.entity_id, **(data or {})}, blocking=True)
            try:
                async with asyncio.timeout(20):
                    if patch["power"] == "off":
                        await call("turn_off")
                    else:
                        if mode is None and temp is not None and state.state == "off":
                            await call("turn_on")
                        if temp is not None:
                            await call("set_temperature", {"temperature": temp, **({"hvac_mode": mode} if mode else {})})
                        elif mode is not None:
                            await call("set_hvac_mode", {"hvac_mode": mode})
                        elif state.state == "off":
                            await call("turn_on")
                        if fan is not None and fan != state.attributes.get("fan_mode"):
                            await call("set_fan_mode", {"fan_mode": fan})
                result["state"] = snapshot(self.hass, [source])[0]
                final = result["state"]
                matched = (final["hvac_mode"] == "off") if patch["power"] == "off" else (
                    final["hvac_mode"] != "off" and (mode is None or final["hvac_mode"] == mode)
                    and (temp is None or final["temperature"] == temp) and (fan is None or final["fan_mode"] == fan))
                if final["available"] and matched:
                    result["status"] = "success"
            except Exception:
                pass  # Do not replay partially executed commands or expose exception data.
            return result
