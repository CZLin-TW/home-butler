"""Bounded Hue commands through HA's already-paired native Hue V2 connection.

No key/host access, arbitrary services, URLs or authentication copying. Selection
is local to HA. All writes use public create_request once: timeouts and partial
execution are unknown, never retried with a different Hue payload.
"""
import asyncio
from collections import OrderedDict
import json
import math
import re
import time

from homeassistant.config_entries import ConfigEntryState
from . import hue_model as model
from . import hue_color

UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
ACTIONS = {"hue.list_areas", "hue.set_state", "hue.recall_scene", "hue.set_effect", "hue.notify", "hue.breathe"}


def native_api(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.domain != "hue" or entry.state != ConfigEntryState.LOADED:
        raise ValueError("Native Hue unavailable")
    bridge = getattr(entry, "runtime_data", None)
    if bridge is None or bridge.api_version != 2 or not bridge.authorized:
        raise ValueError("Native Hue V2 unavailable")
    return bridge.api


async def resources(hass, entry_id):
    async with asyncio.timeout(8):
        data = await native_api(hass, entry_id).request("get", "clip/v2/resource")
    if not isinstance(data, list) or len(data) > 5000:
        raise ValueError("Invalid Hue catalogue")
    result = {kind: [] for kind in ("room", "zone", "bridge_home", "grouped_light", "device", "light", "scene", "smart_scene")}
    for item in data:
        if isinstance(item, dict) and item.get("type") in result and UUID.fullmatch(str(item.get("id", ""))):
            result[item["type"]].append(item)
    return result


async def group_options(hass, previous=()):
    options = {}
    for entry in hass.config_entries.async_entries("hue"):
        try:
            data = await resources(hass, entry.entry_id)
            for area in model.list_areas(data)["areas"]:
                key = entry.entry_id + "/" + area["id"]
                options[key] = {"value": key, "label": f"{area['hue_name'] or area['id']} ({area['kind']}) · {entry.title}"}
        except Exception:
            continue  # Offline bridge must not prevent editing other integration options.
    for key in previous:
        options.setdefault(key, {"value": key, "label": "目前無法讀取 · " + key})
    return list(options.values())


async def select_groups(hass, selected, previous=()):
    if not isinstance(selected, list) or len(selected) > 40 or any(not isinstance(s, str) for s in selected):
        raise ValueError("Invalid Hue selection")
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate Hue groups")
    if not selected:
        return []
    allowed = {item["value"] for item in await group_options(hass, previous)}
    if set(selected) - allowed:
        raise ValueError("Select available native Hue areas")
    return selected


def plan(action, payload, catalogues):
    """Validate the entire target set before any physical side effect."""
    candidates = []
    for entry_id, selected, data in catalogues:
        for area in model.list_areas(data)["areas"]:
            if area["id"] in selected:
                area["color_control"] = hue_color.describe(area, data)
                candidates.append((entry_id, data, area))
    if action == "hue.list_areas":
        if payload:
            raise ValueError("Unexpected list parameters")
        return [], {"areas": [area for _, _, area in candidates], "counts": {"selected_areas": len(candidates)}}
    allowed_keys = {
        "hue.set_state": {"area_id", "resource_type", "on", "brightness", "hs_color", "color_temp_kelvin"},
        "hue.recall_scene": {"scene_id", "resource_type", "action"},
        "hue.set_effect": {"area_id", "resource_type", "effect"},
        "hue.notify": {"area_id", "resource_type", "notification"},
        "hue.breathe": {"resource_id", "resource_type"},
    }
    if set(payload) - allowed_keys[action]:
        raise ValueError("Unexpected parameters")
    if action == "hue.recall_scene":
        rtype, scene_id = payload.get("resource_type", "scene"), payload.get("scene_id")
        matches = [(eid, s) for eid, _, area in candidates for s in area["scenes"]
                   if s["id"] == scene_id and s["resource_type"] == rtype]
        # Same scene may appear only once; ambiguous IDs across bridges are rejected.
        if len(matches) != 1:
            raise ValueError("Scene not in selected area")
        eid, scene = matches[0]
        recall = payload.get("action", "active")
        allowed = {"activate", "deactivate"} if rtype == "smart_scene" else {"active", "static", "dynamic_palette"}
        if recall not in allowed or (recall == "dynamic_palette" and not scene["dynamic_available"]):
            raise ValueError("Unsupported scene action")
        return [(eid, rtype, scene_id, {"recall": {"action": recall}})], {"scene_id": scene_id, "action": recall}
    target = payload.get("resource_id") if action == "hue.breathe" else payload.get("area_id")
    if payload.get("resource_type", "grouped_light") != "grouped_light":
        raise ValueError("Only selected grouped lights are exposed")
    matches = [(eid, data, area) for eid, data, area in candidates if area["id"] == target]
    if len(matches) != 1:
        raise ValueError("Area not selected or ambiguous")
    eid, data, area = matches[0]
    result = {"resource_id": target, "resource_type": "grouped_light"}
    if action == "hue.set_state":
        body = {}
        if payload.get("on") is not None:
            if type(payload["on"]) is not bool:
                raise ValueError("Invalid power")
            body["on"] = {"on": payload["on"]}
        if payload.get("brightness") is not None:
            value = payload["brightness"]
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 100:
                raise ValueError("Invalid brightness")
            body["dimming"] = {"brightness": value}
        color_writes, skipped = hue_color.plan_color(area, data, payload)
        if not body and not color_writes:
            raise ValueError("Empty state command")
        writes = [(eid, "grouped_light", target, body)] if body else []
        writes.extend((eid, "light", lid, color_body) for lid, color_body in color_writes)
        return writes, {**result, "skipped_light_ids": skipped}
    if action in ("hue.notify", "hue.breathe"):
        key = "alert:breathe" if action == "hue.breathe" else payload.get("notification", "alert:breathe")
        matches = [n for n in area["notifications"] if n["key"] == key]
        if len(matches) != 1:
            raise ValueError("Unsupported notification")
        option = matches[0]
        field = "action" if option["kind"] == "alert" else "signal"
        return [(eid, "grouped_light", target, {option["kind"]: {field: option["action"]}})], result
    effect = payload.get("effect")
    if not isinstance(effect, str) or effect not in {x["key"] for x in area["effects"]}:
        raise ValueError("Unsupported effect")
    container = model._hue_container_for_grouped_light_id(target, data)
    lights = {item["id"]: item for item in data["light"]}
    containers = {(item["type"], item["id"]): item for kind in ("room", "zone", "bridge_home") for item in data[kind]}
    ids = model._hue_container_light_ids(container, containers, {i["id"]: i for i in data["device"]}, lights)
    writes, skipped = [], []
    for light_id in ids:
        bodies = model._hue_effect_payloads_for_light(lights[light_id], effect)
        if bodies:
            writes.append((eid, "light", light_id, bodies[0]))
        else:
            skipped.append(light_id)
    if not writes:
        raise ValueError("No supported lights")
    return writes, {**result, "effect": effect, "skipped_light_ids": skipped, "errors": []}


class HueCommands:
    def __init__(self, hass, groups):
        self.hass, self.groups = hass, tuple(groups)
        self.results = OrderedDict()
        self.lock = asyncio.Lock()

    async def execute(self, frame):
        request_id = frame.get("request_id")
        response = {"type": "hue_result", "request_id": request_id, "status": "failed", "result": {}}
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            return response
        async with self.lock:
            if request_id in self.results:
                old, result = self.results[request_id]
                return result if old == frame else response
            self.results[request_id] = (dict(frame), response)
            while len(self.results) > 256:
                self.results.popitem(last=False)
            try:
                if set(frame) != {"type", "request_id", "action", "payload", "expires_at"} or frame["type"] != "hue_command":
                    return response
                expires = frame["expires_at"]
                if type(expires) not in (int, float) or not time.time() < expires <= time.time() + 30:
                    return response
                action, payload = frame["action"], frame["payload"]
                if not isinstance(action, str) or action not in ACTIONS or not isinstance(payload, dict):
                    return response
                async with asyncio.timeout(22):
                    selected = {}
                    for value in self.groups:
                        eid, group_id = value.split("/", 1)
                        selected.setdefault(eid, set()).add(group_id)
                    catalogs = [(eid, groups, await resources(self.hass, eid)) for eid, groups in selected.items()]
                    writes, result = plan(action, payload, catalogs)
                    if len(json.dumps(result).encode()) > 100000:
                        return response
                    applied = []
                    for eid, rtype, rid, body in writes:
                        if time.time() >= expires:
                            return response
                        api = native_api(self.hass, eid)  # Recheck unload/auth before every write.
                        response["status"] = "unknown"
                        response["result"] = {"applied_light_ids": list(applied)}
                        async with api.create_request("put", f"clip/v2/resource/{rtype}/{rid}", json=body) as reply:
                            reply.raise_for_status()
                            data = await reply.json()
                            if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), list):
                                return response
                        applied.append(rid)
                    if action == "hue.set_effect":
                        result["applied_light_ids"] = applied
                    response.update(status="success", result=result)
            except asyncio.CancelledError:
                raise  # Cached unknown survives interrupted writes; no replay on reconnect.
            except Exception:
                pass  # No raw Hue exceptions/credentials leave HA.
            return response
